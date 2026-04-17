#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# monitor_sync_lambda.py
import json
import os
import logging
import boto3
from typing import Dict, Any, List
from datetime import datetime
# Import Decimal from decimal module
from decimal import Decimal


# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
bedrock = boto3.client('bedrock-agent')
dynamodb = boto3.resource('dynamodb')
sns = boto3.client('sns')

# Get environment variables
METADATA_TABLE = os.environ['METADATA_TABLE']
TRACKING_TABLE = os.environ.get('TRACKING_TABLE')
NOTIFICATION_TOPIC = os.environ.get('NOTIFICATION_TOPIC')

# Get DynamoDB tables
metadata_table = dynamodb.Table(METADATA_TABLE)
tracking_table = dynamodb.Table(TRACKING_TABLE) if TRACKING_TABLE else None

def mark_changes_as_processed(job_id: str, change_ids: List[str]) -> int:
    """
    Mark changes as processed with the given job ID.
    
    Args:
        job_id: Ingestion job ID
        change_ids: List of change IDs to mark as processed
        
    Returns:
        Number of changes marked as processed
    """
    if not tracking_table or not change_ids:
        return 0
        
    processed_count = 0
    
    for change_id in change_ids:
        try:
            tracking_table.update_item(
                Key={
                    'change_id': change_id
                },
                UpdateExpression='SET #p = :processed, ingestion_job_id = :job_id',
                ExpressionAttributeNames={
                    '#p': 'processed'
                },
                ExpressionAttributeValues={
                    ':processed': True,
                    ':job_id': job_id
                }
            )
            processed_count += 1
        except Exception as e:
            logger.error(f"Error marking change {change_id} as processed: {str(e)}")
    
    logger.info(f"Marked {processed_count} changes as processed for job {job_id}")
    return processed_count

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Monitor the status of a sync job and update metadata.
    
    Args:
        event: Lambda event object
        context: Lambda context object
        
    Returns:
        Dictionary with job status information
    """
    # Log only safe identifiers — avoid logging full event payloads
    kb_id = event.get('knowledge_base_id', 'unknown')
    job_id = event.get('job_id', 'unknown')
    logger.info(f"Monitoring sync job for kb_id={kb_id}, job_id={job_id}")
    
    try:
        # Extract job information
        kb_id = event.get('knowledge_base_id')
        job_id = event.get('job_id')
        data_source_id = event.get('data_source_id')
        metadata_id = event.get('metadata_id')
        change_ids = event.get('message', {}).get('change_ids', [])
        
        if not (kb_id and job_id):
            raise ValueError("Knowledge base ID and job ID are required")
        
        if not data_source_id:
            raise ValueError("Data source ID is required")
        
        # Get job status
        response = bedrock.get_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id,
            ingestionJobId=job_id
        )
        
        # Extract job status and statistics
        job_status = response['ingestionJob']['status']
        job_stats = response['ingestionJob'].get('statistics', {})
        
        logger.info(f"Sync job {job_id} for knowledge base {kb_id} has status {job_status}")
        
        # Update metadata
        current_time = Decimal(str(datetime.utcnow().timestamp()))
        update_expression = 'SET job_status = :job_status, statistics = :statistics, last_checked = :last_checked'
        expression_values = {
            ':job_status': job_status,
            ':statistics': job_stats,
            ':last_checked': current_time
        }
        
        # Add end_time if job is in terminal state
        is_terminal_state = job_status in ['COMPLETE', 'FAILED', 'STOPPED']
        if is_terminal_state:
            update_expression += ', #job_state = :job_state, end_time = :end_time'
            expression_values[':job_state'] = job_status
            expression_values[':end_time'] = current_time
            expression_attribute_names = {'#job_state': 'status'}
        
        # Update the metadata record
        if is_terminal_state:
            metadata_table.update_item(
                Key={
                    'job_id': job_id
                },
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
                ExpressionAttributeNames=expression_attribute_names
            )
        else:
            metadata_table.update_item(
                Key={
                    'job_id': job_id
                },
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values
            )
        
        # If job completed successfully, mark changes as processed
        if is_terminal_state and job_status == 'COMPLETE':
            processed_count = mark_changes_as_processed(job_id, change_ids)
            logger.info(f"Marked {processed_count} changes as processed for job {job_id}")
        
        # Send notification if configured and job is in terminal state
        if is_terminal_state and NOTIFICATION_TOPIC:
            notification = {
                'knowledge_base_id': kb_id,
                'job_id': job_id,
                'status': job_status,
                'statistics': job_stats,
                'processed_changes': len(change_ids) if job_status == 'COMPLETE' else 0
            }
            
            sns.publish(
                TopicArn=NOTIFICATION_TOPIC,
                Subject=f"Bedrock KB Sync Job {job_status}",
                Message=json.dumps(notification, indent=2)
            )
        
        # Add job status to event
        event['job_status'] = job_status
        event['job_stats'] = job_stats
        event['is_complete'] = is_terminal_state
        
        return event
        
    except Exception as e:
        logger.error(f"Error monitoring sync job: {str(e)}", exc_info=True)
        
        # Update metadata with error if we have job_id
        if 'job_id' in event:
            try:
                metadata_table.update_item(
                    Key={
                        'job_id': event['job_id']
                    },
                    UpdateExpression='SET #job_state = :job_state, error = :error, end_time = :end_time',
                    ExpressionAttributeValues={
                        ':job_state': 'ERROR',
                        ':error': str(e),
                        ':end_time': Decimal(str(datetime.utcnow().timestamp()))
                    },
                    ExpressionAttributeNames={
                        '#job_state': 'status'
                    }
                )
            except Exception as update_error:
                logger.error(f"Error updating metadata: {str(update_error)}")
        
        # Add error to event
        event['error'] = str(e)
        event['status'] = 'ERROR'
        
        return event
