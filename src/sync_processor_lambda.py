#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# sync_processor_lambda.py
import json
import os
import logging
import boto3
import uuid
from typing import Dict, Any
from datetime import datetime
from decimal import Decimal

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
sqs = boto3.client('sqs')
sfn = boto3.client('stepfunctions')
dynamodb = boto3.resource('dynamodb')

# Get environment variables
STEP_FUNCTION_ARN = os.environ['STEP_FUNCTION_ARN']
METADATA_TABLE = os.environ['METADATA_TABLE']

# Get DynamoDB table
metadata_table = dynamodb.Table(METADATA_TABLE)

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Process SQS messages and start Step Functions workflow for full data source ingestion.
    
    Args:
        event: Lambda event object
        context: Lambda context object
        
    Returns:
        Dictionary with processing results
    """
    logger.info(f"Received event with {len(event.get('Records', []))} records")
    
    results = []
    
    try:
        for record in event.get('Records', []):
            # Extract message body
            message_body = record.get('body', '{}')
            message = json.loads(message_body)
            
            # Extract message attributes
            message_attributes = record.get('messageAttributes', {})
            kb_id = message.get('knowledge_base_id')
            
            if not kb_id:
                kb_id = message_attributes.get('KnowledgeBaseId', {}).get('stringValue')
            
            if not kb_id:
                logger.warning("Skipping message without knowledge base ID")
                continue
            
            # Get data source ID - must be exactly 10 alphanumeric characters
            data_source_id = message.get('data_source_id')
            
            # If data_source_id is not provided or doesn't match the required pattern,
            # we need to list data sources for the KB and get the first one
            if not data_source_id or not (len(data_source_id) == 10 and data_source_id.isalnum()):
                try:
                    bedrock = boto3.client('bedrock-agent')
                    ds_response = bedrock.list_data_sources(
                        knowledgeBaseId=kb_id,
                        maxResults=10
                    )
                    
                    if ds_response.get('dataSourceSummaries'):
                        data_source_id = ds_response['dataSourceSummaries'][0]['dataSourceId']
                        logger.info(f"Found data source ID: {data_source_id}")
                    else:
                        logger.error(f"No data sources found for knowledge base {kb_id}")
                        # We'll continue with the message processing, but the Step Functions
                        # workflow will handle this error properly
                except Exception as ds_error:
                    logger.error(f"Error getting data source ID: {str(ds_error)}")
            
            # Start Step Functions workflow for the entire data source
            sfn_input = {
                'knowledge_base_id': kb_id,
                'data_source_id': data_source_id,
                'message': message
            }
            
            # Generate execution name
            execution_name = f"sync-{kb_id}-{int(datetime.utcnow().timestamp())}"
            
            # Start Step Functions execution
            response = sfn.start_execution(
                stateMachineArn=STEP_FUNCTION_ARN,
                name=execution_name,
                input=json.dumps(sfn_input)
            )
            
            logger.info(f"Started Step Functions execution: {response['executionArn']}")
            
            # Create metadata entry for the ingestion job
            # Note: We'll update this with the actual job_id once the ingestion job starts
            metadata = {
                'job_id': f"pending-{uuid.uuid4()}", # Add a temporary job_id
                'execution_arn': response['executionArn'],
                'knowledge_base_id': kb_id,
                'data_source_id': data_source_id,
                'status': 'STARTED',
                'start_time': Decimal(str(datetime.utcnow().timestamp())),
                'change_count': message.get('change_count', 0),
                'change_types': message.get('change_types', {}),
                'source': message.get('source', 'unknown'),
                'reason': message.get('reason', ''),
                'message_id': record['messageId']
            }
            
            # Store initial metadata (will be updated with job_id by Start Sync Lambda)
            metadata_table.put_item(Item=metadata)
            
            results.append({
                'message_id': record['messageId'],
                'knowledge_base_id': kb_id,
                'execution_arn': response['executionArn'],
                'status': 'started'
            })
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f"Processed {len(results)} messages",
                'results': results
            })
        }
        
    except Exception as e:
        logger.error(f"Error processing messages: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': f"Error processing messages: {str(e)}"
            })
        }
