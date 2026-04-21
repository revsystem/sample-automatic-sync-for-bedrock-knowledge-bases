# Amazon Bedrock Knowledge Base Auto-Sync Solution (AWS SAM Version)

Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: MIT-0

This is the AWS SAM (AWS Serverless Application Model) version of the Amazon Bedrock Knowledge Base Auto-Sync Solution. AWS SAM simplifies serverless application development by providing a simplified syntax for defining serverless resources and handling deployment complexities.

## Prerequisites

Before deploying this solution, you need:

1. An existing Amazon Bedrock Knowledge Base
2. An Amazon S3 bucket containing documents to sync, configured with:
   - Block Public Access enabled
   - Default encryption enabled (SSE-S3 or SSE-KMS)
   - Bucket policy requiring SSL/TLS (`aws:SecureTransport` condition)
   - Versioning enabled
   - Server access logging enabled
3. AWS SAM CLI installed
4. AWS CLI configured with appropriate permissions

## Security Considerations

You are responsible for:
- Configuring IAM permissions following least-privilege principles
- Securing your Amazon S3 bucket (encryption, access policies, Block Public Access)
- Securing Amazon SNS topic subscriptions
- Reviewing and customizing IAM policies created by this template
- Managing KMS key access policies

AWS manages:
- Security of the underlying managed services (AWS Lambda, Amazon DynamoDB, Amazon SQS, Amazon SNS, AWS Step Functions)
- Patching and maintenance of managed service platforms

## Deployment Instructions

### 1. Clone the Repository

```bash
git clone <repository-url>
cd <repository-directory>
```

### 2. Build the AWS SAM Application

```bash
sam build
```

This command processes your AWS SAM template, installs dependencies, and prepares your application for deployment.

### 3. Deploy the Application

```bash
sam deploy --guided
```

Follow the prompts to configure your deployment:

- **Stack Name**: Choose a name for your AWS CloudFormation stack
- **AWS Region**: Select the region where your Amazon Bedrock Knowledge Base is located
- **Parameter KnowledgeBaseId**: Enter your Amazon Bedrock Knowledge Base ID
- **Parameter S3BucketName**: Enter the name of your Amazon S3 bucket containing documents
- **Parameter S3KeyPrefix**: (Optional) Enter a prefix for Amazon S3 keys to sync
- **Parameter NotificationsEmail**: (Optional) Enter an email address for notifications
- **Other parameters**: Accept defaults or customize as needed

### 4. (Optional) Update the Stack After Changes to Code, Template, or Configuration

Use this step any time you modify Lambda code (`src/*.py`), the SAM template (`template.yaml`), or the deploy configuration (`samconfig.toml`). Your existing `samconfig.toml` stores the deployment settings, so no `--guided` flag is needed for subsequent deploys.

```bash
# 1. Validate the template (catches syntax and semantic errors early)
sam validate

# 2. Rebuild — repackages Lambda code and resolves template changes
sam build

# 3. Deploy — uses settings from samconfig.toml
sam deploy
```

Because `confirm_changeset = true` is set in `samconfig.toml`, SAM will show you the CloudFormation changeset before applying. Review it and confirm with `y`.

**What triggers what in the changeset:**

| Change you made | What CloudFormation updates |
|---|---|
| Lambda code only (`src/*.py`) | Lambda function code (new S3 artifact uploaded) |
| Template resource properties | The specific resource you modified |
| Template parameters | Any resources that reference the changed parameter |
| New resources in template | Creates the new resource and any dependent resources |

**Tips:**

- If you only changed Lambda code and see no changeset, run `sam build` again — SAM caches builds and may skip unchanged files.
- To override a parameter for a single deploy without editing `samconfig.toml`:
  ```bash
  sam deploy --parameter-overrides "LambdaMemorySize=512"
  ```
- To preview changes without deploying:
  ```bash
  sam deploy --no-execute-changeset
  ```
- If a deploy fails and the stack is in `ROLLBACK_COMPLETE`, delete it first with `sam delete`, then redeploy.

### 5. Confirm Subscription (if using notifications)

If you provided an email address for notifications, you'll receive a confirmation email. Click the confirmation link to start receiving notifications.

## Testing the Solution

1. **Upload a test document to your Amazon S3 bucket**:
   ```bash
   aws s3 cp test-document.pdf s3://your-bucket-name/your-prefix/
   ```

2. **Monitor the process**:
   - Check the Amazon CloudWatch dashboard created by the stack
   - The URL will be in the Outputs section of your AWS CloudFormation stack

3. **Check the sync workflow**:
   - Check the AWS Step Functions execution history in the AWS Console
   - Verify the ingestion job completed successfully in the Amazon Bedrock console

4. **Verify Knowledge Base synchronization**:
   - After the sync job completes, check your Amazon Bedrock knowledge base
   - Verify that the new documents are available for queries

## Solution Architecture

This solution uses an event-driven approach to facilitate document synchronization promptly and reliably:

1. **Event-based processing**: Captures Amazon S3 events (create, update, delete) in real-time
2. **Rate limiting**: Uses Amazon SQS to respect the 0.1 requests/second limit for Amazon Bedrock API calls
3. **AWS Step Functions workflow**: Orchestrates the sync process with proper error handling and retries

## Monitoring and Troubleshooting

- **Amazon CloudWatch dashboard**: Provides visibility into all aspects of the solution
- **Amazon CloudWatch Logs**: Each AWS Lambda function has its own log group
- **Amazon DynamoDB tables**: Track document changes and sync job status
- **Amazon SQS queues**: Check for messages in the dead-letter queue if there are failures
- **AWS Step Functions executions**: View execution history for detailed workflow information

## Cleanup

To remove the solution:

```bash
sam delete
```

This will delete all resources created by the AWS SAM template.

## Additional Resources

- [AWS SAM Documentation](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/what-is-sam.html)
- [Amazon Bedrock Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/what-is-bedrock.html)
- [Amazon S3 Documentation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html)

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.
