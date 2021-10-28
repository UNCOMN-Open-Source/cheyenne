#!/usr/bin/env python3
'''
#
# Ingest function - receives events from S3 and queues object ingestion into the Cheyenne Vault.
#
# Project Cheyenne
#
# @author Damian Bushong <dbushong@uncomn.com>
# @copyright (c) 2021 UNCOMN LLC.
# @license MIT License
#
'''

import json
import logging
from os import environ
import random
import uuid

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

AWS_REGION = environ.get('AWS_REGION')

# these should be AWS SQS queue identifiers
BAD_RECEIVE_QUEUE = environ.get('BAD_RECEIVE_QUEUE') # used for discarded files
DUPLICATOR_QUEUE = environ.get('DUPLICATOR_QUEUE') # used for "small" files (< 1 GiB)
DUPLICATOR_LARGE_QUEUE = environ.get('DUPLICATOR_LARGE_QUEUE') # used for "large" files (> 1 GiB, < 5 GiB)

# should be an AWS Batch job queue identifier
DUPLICATOR_GIANT_QUEUE = environ.get('DUPLICATOR_GIANT_QUEUE', False) # used for "giant" files (> 5 GiB)

# used with the rotating message group slot to get some level of concurrency with AWS SQS FIFO queues
MAX_DUPLICATOR_CONCURRENCY = int(environ.get('MAX_DUPLICATOR_CONCURRENCY') or 5)

LARGE_THRESHOLD = 1024 * 1024 * 1024 * 1 # 1 GiB
GIANT_THRESHOLD = 1024 * 1024 * 1024 * 5 # 5 GiB

# initialize rotating message_group_slot with a random offset; this helps level out distribution
#   somewhat across all slots when we're trying to parallelize
message_group_slot = random.randint(0, MAX_DUPLICATOR_CONCURRENCY - 1)
logger.debug('generated random message_group_slot', extra={
    'message_group_slot': message_group_slot,
    'MAX_DUPLICATOR_CONCURRENCY' : MAX_DUPLICATOR_CONCURRENCY
})

QUEUES = {
    'standard': DUPLICATOR_QUEUE,
    'large': DUPLICATOR_LARGE_QUEUE,
    'batch': DUPLICATOR_GIANT_QUEUE,
    'failure': BAD_RECEIVE_QUEUE
}

def rotate_message_group_slot():
    '''
    Rotate the message group slot # (incrementing and looping around as needed) to
    force concurrency with a Lambda triggered by a SQS FIFO queue.

    Necessary in order to overcome SQS FIFO queue concurrency limitations by using rotating
    MessageGroupId values.
    '''
    global message_group_slot # pylint: disable=global-statement,invalid-name

    message_group_slot += 1
    if message_group_slot >= MAX_DUPLICATOR_CONCURRENCY:
        message_group_slot = 0

    logger.debug('rotated message_group_slot', extra={
        'message_group_slot': message_group_slot,
        'MAX_DUPLICATOR_CONCURRENCY' : MAX_DUPLICATOR_CONCURRENCY
    })

def lambda_handler(event, _context):
    ''' Lambda handler. '''

    messages = {
        'standard': [],
        'large': [],
        'batch': [],
        'failure': []
    }

    for record in event['Records']:
        try:
            #
            # generate a UUIDv4 for a unique reference that will flow downstream through
            #   all descendent requests - this should be inserted into ALL logging to better enable multi-microservice debugging
            #
            vault_event_uuid = str(uuid.uuid4())
            record['vault_event_uuid'] = vault_event_uuid

            # expunge the source IP address to avoid any privacy concerns
            record['requestParameters']['sourceIPAddress'] = 'removed'

            # ensuring that we cannot bust the maximum key length for an S3 object
            #   (which is possible if the original was within ~60 bytes of the maximum)
            if len(record['s3']['bucket']['name'] + '/' + record['s3']['object']['key']) > 1024:
                raise ValueError('Cannot copy record, maximum key length exceeded.')

            #
            # the capability to handle files over 5GiB (the maximum for an s3.copy_object) is currently not yet available,
            # so for now we're ignoring these files. we expect to be able to pull this off with AWS Batch
            #
            # todo: remove pylint disable once AWS Batch is implemented
            if record['s3']['object']['size'] >= GIANT_THRESHOLD: # pylint: disable=no-else-raise
                # self-disable AWS batch integration if no job queue was configured for dispatch
                if DUPLICATOR_GIANT_QUEUE is False:
                    logger.warning('Object too large to currently copy, ignoring', extra={ 'vault_event_uuid': vault_event_uuid })

                    raise ValueError('Object too large to process')

                # todo: handle with AWS Batch
                raise NotImplementedError('AWS Batch support not yet implemented')

                # logger.info('Object surpasses GIANT_THRESHOLD, sending to AWS Batch job queue', extra={
                #     'vault_event_uuid': vault_event_uuid,
                #     'object': record['s3']['object']
                # })
            elif record['s3']['object']['size'] >= LARGE_THRESHOLD:
                logger.info('Object surpasses LARGE_THRESHOLD, sending to large queue', extra={
                    'vault_event_uuid': vault_event_uuid,
                    'object': record['s3']['object']
                })
                messages['large'].append({
                    'Id': vault_event_uuid,
                    'MessageBody': json.dumps(record),
                    'MessageGroupId': 'vault' + str(message_group_slot)
                })
            else:
                messages['standard'].append({
                    'Id': vault_event_uuid,
                    'MessageBody': json.dumps(record),
                    'MessageGroupId': 'vault' + str(message_group_slot)
                })
        except Exception: # pylint: disable=broad-except
            logger.exception('Failed to process record', extra={ 'vault_event_uuid': vault_event_uuid })
            messages['failure'].append({
                'Id': vault_event_uuid,
                'MessageBody': json.dumps(record)
            })

    for queue_type, entries in messages.items():
        if len(entries) > 0 and queue_type != 'batch':
            logger.info('Dispatching records to queue', extra={
                'queue_type': queue_type,
                'total_records': len(entries),
                'vault_event_uuids': [entry['Id'] for entry in entries]
            })
            sqs.send_message_batch(
                QueueUrl=QUEUES[queue_type],
                Entries=entries
            )

        if len(entries) > 0 and queue_type == 'batch':
            # todo: handle with AWS Batch
            pass

    rotate_message_group_slot()
