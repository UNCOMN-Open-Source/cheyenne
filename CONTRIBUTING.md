# Contributing Guidelines

First - thank you for your initiative!  Contributions to this project are quite welcome, but we do need to set some guidelines down to keep things organized.

## Did you find a bug?

- If it's a security vulnerability, please contact us directly; we strongly advocate for coordinated disclosure.
- Please ensure the bug was not already reported by searching our [GitHub issues list](https://github.com/uncomnllc/cheyenne/issues).
- If you're unable to find an open issue addressing the problem, [open a new one](https://github.com/uncomnllc/cheyenne/issues/new). Be sure to include a title and clear description, as much relevant information as possible, and a code sample or an executable test case demonstrating the expected behavior that is not occurring.


## Issues open to contribution

Want to contribute but don't know where to start? Have a look at the issues labeled with the `good first issue` label.

## Code Conventions

We strongly recommend the use of an `.editorconfig` compliant IDE or IDE plugin.

### CloudFormation Templates

CloudFormation templates must:

* be written in YAML
* use the `.yml` extension and not `.template`
* use 2-space indentation
* use UTF-8 encoding
* use `String` types for AWS Account IDs
* quote all strings to avoid situations like [The Norway Problem](https://hitchdev.com/strictyaml/why/implicit-typing-removed/), [Christopher Null](http://www.wired.com/2015/11/null), and better handle [AWS Account IDs with leading zeroes](https://blog.rowanudell.com/aws-accounts-with-leading-zeros/)
* use only `true` and `false` for booleans
* not indent for entries in a list e.g.:
```yaml
list:
- 'entry1'
- 'entry2'
```
* use `!Functions` over `Fn::Functions` syntax when possible
* prefer `!Sub` over `!GetAttr` and `!Join`
* utilize builtin parameters in ARNs like `AWS::Partition`, `AWS::URLSuffix`, etc. (for portability with regions such as AWS GovCloud)
* explicitly declare all resource dependencies using `DependsOn`

### Python

Python code must:

* pass `pylint` without modifications to pylint rules or adding `pylint disable=` directives without explicit maintainer approval
* not introduce new dependencies without maintainer approval (due to risk assessment and security approvals)
* when using `boto3.client` calls, they should be assigned to a variable named after the service itself (or a reasonable abbreviation, such as `ssm`)
* log statements should include the `vault_event_uuid` for traceability
* comments should focus on explaining the ***why*** unless the code is arcane out of necessity and requires explanation
