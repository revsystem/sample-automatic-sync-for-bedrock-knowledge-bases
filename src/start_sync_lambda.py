#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# start_sync_lambda.py
import json
import os
import logging
import boto3
from typing import Dict, Any

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
bedrock = boto3.client('bedrock-agent')
dynamodb = boto3.resource('dynamodb')

# Get environment variables
METADATA_TABLE = os.environ['METADATA_TABLE']

# Get DynamoDB table
metadata_table = dynamodb.Table(METADATA_TABLE)

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Start an ingestion job for the entire data source.
    
    Args:
        event: Lambda event object
        context: Lambda context object
        
    Returns:
        Dictionary with sync job information
    """
    # Log only safe identifiers — avoid logging full event payloads
    kb_id = event.get('knowledge_base_id', 'unknown')
    data_source_id = event.get('data_source_id', 'unknown')
    logger.info(f"Starting sync job for kb_id={kb_id}, data_source_id={data_source_id}")
    
    try:
        # Extract knowledge base ID and quota check
        kb_id = event.get('knowledge_base_id')
        data_source_id = event.get('data_source_id')
        execution_arn = event.get('execution_arn')
        quota_check = event.get('quota_check', {})
        
        if not kb_id:
            raise ValueError("Knowledge base ID is required")
            
        if not data_source_id:
            data_source_id = f"{kb_id}-s3-datasource"
        
        # Check if quotas are OK
        if not quota_check.get('all_quotas_ok', False):
            logger.warning(f"Cannot start sync job due to quota limits: {quota_check}")
            
            # Update metadata with quota exceeded status
            if execution_arn:
                # Find metadata record by execution ARN
                response = metadata_table.scan(
                    FilterExpression='execution_arn = :execution_arn',
                    ExpressionAttributeValues={
                        ':execution_arn': execution_arn
                    }
                )
                
                items = response.get('Items', [])
                if items:
                    metadata_table.update_item(
                        Key={
                            'job_id': items[0].get('job_id', 'pending')
                        },
                        UpdateExpression='SET status = :status, quota_check = :quota_check',
                        ExpressionAttributeValues={
                            ':status': 'QUOTA_EXCEEDED',
                            ':quota_check': quota_check
                        }
                    )
            
            # Add status to event
            event['status'] = 'QUOTA_EXCEEDED'
            return event
        
        # Start the ingestion job for the entire data source
        response = bedrock.start_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id
        )
        
        # Extract job information
        job_id = response['ingestionJob']['ingestionJobId']
        job_status = response['ingestionJob']['status']
        
        logger.info(f"Started sync job {job_id} for knowledge base {kb_id} with status {job_status}")
        
        # Update metadata with job ID and status
        if execution_arn:
            # Find metadata record by execution ARN
            scan_response = metadata_table.scan(
                FilterExpression='execution_arn = :execution_arn',
                ExpressionAttributeValues={
                    ':execution_arn': execution_arn
                }
            )
            
            items = scan_response.get('Items', [])
            if items:
                # Create new record with job_id as key
                metadata = items[0]
                metadata['job_id'] = job_id
                metadata['job_status'] = job_status
                
                # Put the updated record
                metadata_table.put_item(Item=metadata)
                
                # Delete the old record if it had a temporary key
                if 'job_id' in items[0] and items[0]['job_id'] != job_id:
                    metadata_table.delete_item(
                        Key={
                            'job_id': items[0]['job_id']
                        }
                    )
        
        # Add job information to event
        event['job_id'] = job_id
        event['job_status'] = job_status
        event['status'] = 'JOB_STARTED'
        
        return event
        
    except Exception as e:
        logger.error(f"Error starting sync job: {str(e)}", exc_info=True)
        
        # Update metadata with error
        if 'execution_arn' in event:
            try:
                # Find metadata record by execution ARN
                response = metadata_table.scan(
                    FilterExpression='execution_arn = :execution_arn',
                    ExpressionAttributeValues={
                        ':execution_arn': event['execution_arn']
                    }
                )
                
                items = response.get('Items', [])
                if items:
                    metadata_table.update_item(
                        Key={
                            'job_id': items[0].get('job_id', 'error')
                        },
                        UpdateExpression='SET status = :status, error = :error',
                        ExpressionAttributeValues={
                            ':status': 'ERROR',
                            ':error': str(e)
                        }
                    )
            except Exception as update_error:
                logger.error(f"Error updating metadata: {str(update_error)}")
        
        # Add error to event
        event['error'] = str(e)
        event['status'] = 'ERROR'
        
        return event
