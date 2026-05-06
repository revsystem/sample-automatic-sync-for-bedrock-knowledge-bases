#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# event_processor_lambda.py
import json
import os
import logging
import boto3
import uuid
from typing import Dict, Any
from datetime import datetime
# Import Decimal from decimal module
from decimal import Decimal

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
sqs = boto3.client('sqs')
dynamodb = boto3.resource('dynamodb')

# Get environment variables
QUEUE_URL = os.environ['QUEUE_URL']
KB_PREFIX_MAPPING = json.loads(os.environ.get('KB_PREFIX_MAPPING', '{}'))
TRACKING_TABLE = os.environ.get('TRACKING_TABLE')

# Get DynamoDB table
tracking_table = dynamodb.Table(TRACKING_TABLE) if TRACKING_TABLE else None

def validate_s3_key(key: str) -> bool:
    """
    Validate Amazon S3 object key for safety.
    
    Args:
        key: Amazon S3 object key
        
    Returns:
        True if key is valid, False otherwise
    """
    if not key or not isinstance(key, str):
        return False
    # Check for path traversal patterns
    if '..' in key or key.startswith('/'):
        logger.warning(f"Rejected S3 key with suspicious pattern: {key}")
        return False
    return True

def extract_kb_id_from_key(key: str) -> str:
    """
    Extract knowledge base ID from Amazon S3 key based on prefix mapping.
    
    Args:
        key: Amazon S3 object key
        
    Returns:
        Knowledge base ID or None if can't be determined
    """
    if not validate_s3_key(key):
        return None

    # Check if the key matches any of our configured prefixes
    for prefix, kb_id in KB_PREFIX_MAPPING.items():
        if key.startswith(prefix):
            return kb_id
    
    # Default fallback: use first part of the path as KB ID
    parts = key.split('/')
    if len(parts) >= 1:
        return parts[0]
    
    return None

def get_change_type(event_name: str) -> str:
    """
    Map S3 event name to change type.

    Accepts both EventBridge ``detail-type`` values
    (``Object Created`` / ``Object Deleted`` / ``Object Restore Completed``)
    and S3 direct notification ``eventName`` values
    (``ObjectCreated:*`` / ``ObjectRemoved:*`` / ``ObjectRestore:Completed``).

    Args:
        event_name: S3 event name (EventBridge detail-type or S3 eventName)

    Returns:
        Change type (``create`` or ``delete``), or ``unknown`` if unsupported.
    """
    if event_name == 'Object Created':
        return 'create'
    if event_name == 'Object Deleted':
        return 'delete'
    if event_name == 'Object Restore Completed':
        return 'create'

    if event_name.startswith('ObjectCreated'):
        return 'create'
    if event_name.startswith('ObjectRemoved'):
        return 'delete'
    if event_name == 'ObjectRestore:Completed':
        return 'create'  # Restored objects are treated as new

    return 'unknown'

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Process Amazon S3 events, track changes in Amazon DynamoDB, and notify Amazon SQS.
    
    Args:
        event: Lambda event object
        context: Lambda context object
        
    Returns:
        Dictionary with processing results
    """
    # Log only safe metadata — avoid logging full event payloads that may contain S3 keys, bucket names, or KB IDs
    record_count = len(event.get('Records', []))
    has_detail = 'detail' in event
    logger.info(f"Received event with {record_count} records, eventbridge={has_detail}")
    
    try:
        # Extract records from the event
        records = []
        
        # Handle Amazon EventBridge events from Amazon S3
        if 'detail' in event and 'object' in event.get('detail', {}):
            # This is an EventBridge event
            detail = event['detail']
            bucket = detail.get('bucket', {}).get('name', '')
            key = detail.get('object', {}).get('key', '')
            event_name = event.get('detail-type', '')
            
            if bucket and key:
                records.append({
                    'eventName': event_name,
                    'eventTime': event.get('time', ''),
                    's3': {
                        'bucket': {'name': bucket},
                        'object': {'key': key}
                    }
                })
        
        # Handle direct S3 events
        elif 'Records' in event:
            records = event['Records']
        
        # Process each record
        processed_count = 0
        for record in records:
            # Extract S3 information
            if 's3' not in record:
                logger.warning("Skipping non-S3 record")
                continue
                
            s3_info = record['s3']
            bucket = s3_info.get('bucket', {}).get('name', '')
            key = s3_info.get('object', {}).get('key', '')
            event_name = record.get('eventName', '')
            
            if not (bucket and key):
                logger.warning("Skipping record with missing bucket or key")
                continue
            
            # Determine change type
            change_type = get_change_type(event_name)
            
            # Extract knowledge base ID
            kb_id = extract_kb_id_from_key(key)
            if not kb_id:
                logger.warning(f"Could not determine knowledge base ID for key: {key}")
                continue
            
            # Create change record in DynamoDB if table is configured
            if tracking_table:
                try:
                    # Generate a unique ID for the change
                    change_id = str(uuid.uuid4())

                    # Create change record
                    tracking_table.put_item(
                        Item={
                            'change_id': change_id,
                            'knowledge_base_id': kb_id,
                            'change_type': change_type,
                            'key': key,
                            'bucket': bucket,
                            'timestamp': Decimal(str(datetime.utcnow().timestamp())),
                            'processed': False,
                            'event_time': record.get('eventTime', '')
                        }
                    )
                    logger.info(f"Created change record for document: {key}")
                except Exception as e:
                    logger.error(f"Error creating change record: {str(e)}")

            # Create message for SQS to notify about the change
            message = {
                'change_type': change_type,
                'bucket': bucket,
                'key': key,
                'knowledge_base_id': kb_id,
                'event_time': record.get('eventTime', ''),
                'source': 'event_processor'
            }
            
            # Send message to SQS
            response = sqs.send_message(
                QueueUrl=QUEUE_URL,
                MessageBody=json.dumps(message),
                MessageAttributes={
                    'ChangeType': {
                        'DataType': 'String',
                        'StringValue': change_type
                    },
                    'KnowledgeBaseId': {
                        'DataType': 'String',
                        'StringValue': kb_id
                    }
                }
            )
            
            logger.info(f"Sent change notification to SQS: {response['MessageId']}")
            processed_count += 1
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f"Processed {processed_count} records",
                'processed_count': processed_count
            })
        }
        
    except Exception as e:
        logger.error(f"Error processing event: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': f"Error processing event: {str(e)}"
            })
        }
