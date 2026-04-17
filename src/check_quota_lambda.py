#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# check_quota_lambda.py
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
s3 = boto3.client('s3')

# Get environment variables
METADATA_TABLE = os.environ['METADATA_TABLE']

# Get DynamoDB table
metadata_table = dynamodb.Table(METADATA_TABLE)

# Service quotas
MAX_CONCURRENT_JOBS_PER_ACCOUNT = 55
MAX_CONCURRENT_JOBS_PER_DATA_SOURCE = 1
MAX_CONCURRENT_JOBS_PER_KB = 1
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024 * 1024  # 50 GB in bytes
MAX_TOTAL_SIZE_BYTES = 100 * 1024 * 1024 * 1024  # 100 GB in bytes

def check_file_size_limits(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Check if files in the sync job exceed size limits.
    
    Args:
        event: Event containing message with file information
        
    Returns:
        Dictionary with file size check results
    """
    message = event.get('message', {})
    bucket = message.get('bucket')
    
    # If we don't have file information, skip the check
    if not bucket:
        return {
            'file_size_check_performed': False,
            'all_files_within_limits': True,
            'reason': 'No file information available'
        }
    
    # Check if we have specific files to check
    files_to_check = message.get('files', [])
    
    # If no specific files, return success
    if not files_to_check:
        return {
            'file_size_check_performed': False,
            'all_files_within_limits': True,
            'reason': 'No specific files to check'
        }
    
    # Check each file
    total_size = 0
    oversized_files = []
    
    for file_info in files_to_check:
        key = file_info.get('key')
        
        if not key or not isinstance(key, str):
            continue

        # Validate key format
        if '..' in key or key.startswith('/'):
            logger.warning(f"Skipping file with suspicious key pattern: {key}")
            continue
            
        try:
            # Get file metadata from S3
            response = s3.head_object(Bucket=bucket, Key=key)
            file_size = response.get('ContentLength', 0)
            
            # Check if file exceeds individual limit
            if file_size > MAX_FILE_SIZE_BYTES:
                oversized_files.append({
                    'key': key,
                    'size_bytes': file_size,
                    'size_gb': file_size / (1024**3)
                })
                continue
                
            # Add to total size
            total_size += file_size
            
        except Exception as e:
            logger.warning(f"Error checking file size for {key}: {str(e)}")
    
    # Check results
    exceeds_total_size = total_size > MAX_TOTAL_SIZE_BYTES
    has_oversized_files = len(oversized_files) > 0
    
    return {
        'file_size_check_performed': True,
        'all_files_within_limits': not (exceeds_total_size or has_oversized_files),
        'total_size_bytes': total_size,
        'total_size_gb': total_size / (1024**3),
        'exceeds_total_size': exceeds_total_size,
        'has_oversized_files': has_oversized_files,
        'oversized_files': oversized_files
    }

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Check if starting a new sync job would exceed service quotas.
    
    Args:
        event: Lambda event object
        context: Lambda context object
        
    Returns:
        Dictionary with quota check results
    """
    # Log only safe identifiers — avoid logging full event payloads
    kb_id = event.get('knowledge_base_id', 'unknown')
    data_source_id = event.get('data_source_id', 'unknown')
    logger.info(f"Checking service quotas for kb_id={kb_id}, data_source_id={data_source_id}")
    
    try:
        # Extract knowledge base ID and data source ID
        kb_id = event.get('knowledge_base_id')
        data_source_id = event.get('data_source_id')
        metadata_id = event.get('metadata_id')
        
        if not kb_id:
            raise ValueError("Knowledge base ID is required")
        
        if not data_source_id:
            raise ValueError("Data source ID is required")
        
        # Check file size limits
        file_size_check = check_file_size_limits(event)
        
        # If files exceed size limits, fail the check
        if file_size_check.get('file_size_check_performed', False) and not file_size_check.get('all_files_within_limits', True):
            logger.warning(f"Files exceed size limits: {file_size_check}")
            
            # Update metadata with file size check results
            if metadata_id:
                metadata_table.update_item(
                    Key={
                        'metadata_id': metadata_id
                    },
                    UpdateExpression='SET file_size_check = :file_size_check, status = :status',
                    ExpressionAttributeValues={
                        ':file_size_check': file_size_check,
                        ':status': 'SIZE_LIMIT_EXCEEDED'
                    }
                )
            
            # Add file size check to event
            event['file_size_check'] = file_size_check
            event['quota_check'] = {
                'account_quota_ok': True,
                'kb_quota_ok': True,
                'data_source_quota_ok': True,
                'all_quotas_ok': False,  # Fail the quota check due to file size
                'reason': 'File size limits exceeded'
            }
            
            return event
        
        # Get current ingestion jobs
        response = bedrock.list_ingestion_jobs(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id,
            maxResults=100
        )
        
        # Count active jobs
        active_jobs = [job for job in response.get('ingestionJobSummaries', []) 
                      if job['status'] in ['STARTING', 'IN_PROGRESS']]
        
        # Count by different dimensions
        total_active_jobs = len(active_jobs)
        kb_active_jobs = sum(1 for job in active_jobs 
                           if job['knowledgeBaseId'] == kb_id)
        ds_active_jobs = sum(1 for job in active_jobs 
                           if job.get('dataSourceId') == data_source_id)
        
        # Check against quotas
        quota_check = {
            'account_quota_ok': total_active_jobs < MAX_CONCURRENT_JOBS_PER_ACCOUNT,
            'kb_quota_ok': kb_active_jobs < MAX_CONCURRENT_JOBS_PER_KB,
            'data_source_quota_ok': ds_active_jobs < MAX_CONCURRENT_JOBS_PER_DATA_SOURCE,
            'all_quotas_ok': (total_active_jobs < MAX_CONCURRENT_JOBS_PER_ACCOUNT and
                             kb_active_jobs < MAX_CONCURRENT_JOBS_PER_KB and
                             ds_active_jobs < MAX_CONCURRENT_JOBS_PER_DATA_SOURCE)
        }
        
        # Add file size check to quota check
        if file_size_check.get('file_size_check_performed', False):
            quota_check['file_size_check'] = file_size_check
            quota_check['all_quotas_ok'] = quota_check['all_quotas_ok'] and file_size_check.get('all_files_within_limits', True)
        
        # Update metadata with quota check results
        if metadata_id:
            metadata_table.update_item(
                Key={
                    'metadata_id': metadata_id
                },
                UpdateExpression='SET quota_check = :quota_check',
                ExpressionAttributeValues={
                    ':quota_check': quota_check
                }
            )
        
        # Add quota check to event
        event['quota_check'] = quota_check
        
        return event
        
    except Exception as e:
        logger.error(f"Error checking service quotas: {str(e)}", exc_info=True)
        
        # Update metadata with error
        if 'metadata_id' in event:
            try:
                metadata_table.update_item(
                    Key={
                        'metadata_id': event['metadata_id']
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
        event['quota_check'] = {
            'account_quota_ok': False,
            'kb_quota_ok': False,
            'data_source_quota_ok': False,
            'all_quotas_ok': False,
            'error': str(e)
        }
        
        return event
