#!/usr/bin/env python3
'''
#
# Duplicator function - receives events from the Ingest function (via SQS) and copies objects into the Cheyenne Vault.
#
# Project Cheyenne
#
# @author Damian Bushong <dbushong@uncomn.com>
# @copyright (c) 2021 UNCOMN LLC.
# @license MIT License
#
'''

from datetime import datetime, timezone
import json
import logging
from os import environ

import boto3
from pythonjsonlogger import jsonlogger

logger = logging.getLogger()
logger.setLevel(logging.DEBUG if environ.get('DEBUG_MODE', 'false').lower() == 'true' else logging.INFO)

# disable verbose logging for botocore, urllib3 to abate debug spam when
#   we're more focused on debug output from this Lambda function itself.
if environ.get('SUPER_DEBUG_MODE', 'false').lower() != 'true':
    logging.getLogger('botocore').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

class CustomJsonFormatter(jsonlogger.JsonFormatter):
    ''' Customizations for JSON formatting. '''
    def add_fields(self, log_record, record, message_dict):
        ''' Add some additional fields for our use. '''
        super().add_fields(log_record, record, message_dict)

        if log_record.get('level'):
            log_record['level'] = log_record['level'].upper()
        else:
            log_record['level'] = record.levelname

        if log_record.get('exc_info'):
            log_record['exc_info'] = log_record['exc_info'].split('\n')

# forcing the json formatter into logger to override the stock formatter Lambda configured by default
for handler in logger.handlers:
    handler.setFormatter(CustomJsonFormatter(timestamp=True))

sqs = boto3.client('sqs')
s3 = boto3.client('s3') # pylint: disable=invalid-name

AWS_REGION = environ.get('AWS_REGION')
VAULT_BUCKET = environ.get('VAULT_BUCKET')

def build_queue_url_from_arn(queue_arn):
    ''' Build an AWS SQS Queue URL from the AWS SQS Queue's ARN. '''
    split_arn = queue_arn.split(':')
    queue_region = split_arn[3]
    queue_account_id = split_arn[4]
    queue_name = split_arn[5]

    return f'https://sqs.{queue_region}.amazonaws.com/{queue_account_id}/{queue_name}'

def tag_set_to_dict(tags):
    ''' Reshapes returned AWS tag structures into a much easier to use Python dict. '''
    return { t['Key']:t['Value'] for t in tags }

def dict_to_tag_set(tags):
    ''' Used to convert a python dictionary into the usual AWS TagSet format. '''
    return [{ 'Key': k, 'Value': v } for k,v in tags.items()]

def lambda_handler(event, _context): # pylint: disable=too-many-locals
    ''' Lambda handler. '''

    accepted_records = []
    failed_records = []
    for record in event['Records']:
        try:
            logger.debug('raw record', extra={ 'record': record })

            s3_event = json.loads(record['body'])

            queue_name = record['eventSourceARN'].split(':')[5]
            vault_event_uuid = s3_event['vault_event_uuid']
            record['vault_event_uuid'] = vault_event_uuid

            origin_object = {
                'Bucket': s3_event['s3']['bucket']['name'],
                'Key': s3_event['s3']['object']['key']
            }

            if 'versionId' in s3_event['s3']['object']:
                origin_object['VersionId'] = s3_event['s3']['object']['versionId']

            logger.debug('event received', extra={
                'vault_event_uuid': vault_event_uuid,
                'queue': queue_name,
                'event': s3_event
            })

            object_tag_response = s3.get_object_tagging(**origin_object)
            origin_tags = tag_set_to_dict(object_tag_response['TagSet'])
            logger.debug('fetched object tags', extra={
                'vault_event_uuid': vault_event_uuid,
                'object': origin_object,
                'tags': origin_tags
            })

            storage_class = 'GLACIER' if origin_tags.get('uncomn:cheyenne:VaultStorage', '').lower() == 'glacier' else 'STANDARD_IA'
            record_processed_time = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(timespec='milliseconds')
            dest_key = f'{origin_object["Bucket"]}/{origin_object["Key"]}'
            vault_tags = {
                'uncomn:cheyenne:VaultEventUUID': vault_event_uuid,
                'uncomn:cheyenne:VaultEventProcessedTime': record_processed_time,
                'uncomn:cheyenne:VaultProcessing': 'COMPLETED'
            }

            logger.debug('about to copy object', extra={
                'vault_event_uuid': vault_event_uuid,
                'from': origin_object,
                'to': {
                    'Bucket': VAULT_BUCKET,
                    'Key': dest_key,
                    'Tags': vault_tags
                },
                'storage_class': storage_class
            })

            copy_response = s3.copy_object(
                Bucket=VAULT_BUCKET,
                Key=dest_key,
                CopySource=origin_object,
                StorageClass=storage_class,
                TaggingDirective='REPLACE',
                Tagging='&'.join([f'{k}={v}' for k,v in vault_tags.items()])
            )

            logger.info('successfully copied object', extra={
                'vault_event_uuid': vault_event_uuid,
                'from': origin_object,
                'to': {
                    'Bucket': VAULT_BUCKET,
                    'Key': dest_key,
                    'VersionId': copy_response['VersionId'],
                    'KMSKeyId': copy_response['SSEKMSKeyId']
                },
                'storage_class': storage_class
            })

            # tagging the original file for traceability and completion feedback
            target_tag_set = {
                **origin_object,
                'Tagging': {
                    'TagSet': dict_to_tag_set({ **origin_tags, **vault_tags })
                }
            }
            s3.put_object_tagging(**target_tag_set)
            logger.debug('tagged source object', extra={
                'vault_event_uuid': vault_event_uuid,
                'processed_time': record_processed_time,
                'object': origin_object
            })

            accepted_records.append(record)
        except Exception: # pylint: disable=broad-except
            logger.exception('Failed to process record', extra={
                'message_id': record['messageId'] if 'messageId' in record else 'UNKNOWN',
                'vault_event_uuid': vault_event_uuid
            })
            failed_records.append(record)

    if len(failed_records) > 0:
        #
        # SQS + Lambda is...weird.
        # In the event of a partial failure, you need to delete the messages successfully
        #   processed yourself, and then throw within the lambda to indicate that the messages
        #   remaining that were to be processed need to be tossed back into the queue and retried.
        #
        logger.info(f'Directly deleting {len(accepted_records)} messages from upstream queue', extra={
            'vault_event_uuids': [record['vault_event_uuid'] for record in accepted_records]
        })

        delete_message_entries = {}
        for record in accepted_records:
            queue_arn = record['eventSourceARN']

            if queue_arn not in delete_message_entries:
                delete_message_entries[queue_arn] = []

            delete_message_entries[queue_arn].append({
                'Id': record['messageId'],
                'ReceiptHandle': record['receiptHandle']
            })

        for queue_arn, entries in delete_message_entries.items():
            logger.info('Accepted one or more records', extra={
                'queue_arn': queue_arn,
                'message_ids': [entry['messageId'] for entry in entries]
            })
            sqs.delete_message_batch(
                QueueURL=build_queue_url_from_arn(queue_arn),
                Entries=entries
            )

        logger.debug('failed records', extra={ 'records': failed_records })
        logger.error('Failed to process one or more records', extra={
            'message_ids': [record['messageId'] for record in failed_records]
        })
        raise Exception('Failed to process one or more records')
