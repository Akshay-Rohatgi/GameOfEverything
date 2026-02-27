import boto3
import json
import base64
import urllib.request
import sys
import os
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError

def enable_bedrock_model_access(model_ids, region=None):
    # Auto-detect region if not specified
    if region is None:
        # Check AWS_REGION first (standard env var used by AWS CLI)
        region = os.environ.get('AWS_REGION')
        if not region:
            # Fall back to boto3 session detection
            session = boto3.Session()
            region = session.region_name or 'us-east-1'

    print(f"Using region: {region}")
    bedrock_client = boto3.client('bedrock', region_name=region)

    print("Checking use case status...")
    # Submit use case (one time per account)
    try:
        bedrock_client.get_use_case_for_model_access()
        print("✓ Use case already exists")
    except ClientError as e:
        if "You have not filled out the request form" not in str(e):
            raise
        print("Submitting use case...")
        form_data = {
            "companyName": "Software Aurora Lab",
            "companyWebsite": "https://ics.uci.edu/",
            "intendedUsers": "1",  # 0 = internal employees
            "industryOption": "Research",
            "otherIndustryOption": "",
            "useCases": "Researching automated cybersecurity competition environments and agentic RAG pipelines to develop vulnerable systems for educational purposes."
        }
        try:
            # For boto3, do NOT base64 encode (only CLI needs base64)
            bedrock_client.put_use_case_for_model_access(formData=json.dumps(form_data))
            print("✓ Use case submitted")
        except ClientError as form_error:
            # If it fails in current region, try us-east-1 (use case might be global)
            if region != 'us-east-1':
                print(f"Use case submission failed in {region}, trying us-east-1...")
                us_east_client = boto3.client('bedrock', region_name='us-east-1')
                try:
                    us_east_client.put_use_case_for_model_access(formData=json.dumps(form_data))
                    print("✓ Use case submitted in us-east-1")
                except ClientError as us_error:
                    print(f"ERROR: Use case submission failed in both {region} and us-east-1: {str(us_error)}")
                    raise
            else:
                print(f"ERROR: Use case submission failed: {str(form_error)}")
                raise

    for model_id in model_ids:
        print(f"\nProcessing {model_id}...")

        # Create agreement (if needed)
        try:
            offers = bedrock_client.list_foundation_model_agreement_offers(modelId=model_id)
            if offers.get('offers'):
                offer_token = offers['offers'][0]['offerToken']
                bedrock_client.create_foundation_model_agreement(
                    modelId=model_id,
                    offerToken=offer_token
                )
                print(f"✓ Agreement created for {model_id}")
        except ClientError as e:
            if "Agreement not supported for this model" in str(e):
                print(f"✓ {model_id} doesn't need an agreement")
            elif "Agreement already exists" in str(e):
                print(f"✓ Agreement already exists for {model_id}")
            else:
                raise

        # Request entitlement
        session = boto3.Session()
        creds = session.get_credentials().get_frozen_credentials()

        url = f'https://bedrock.{region}.amazonaws.com/foundation-model-entitlement'
        request = AWSRequest(
            method='POST',
            url=url,
            data=json.dumps({'modelId': model_id}),
            headers={'Content-Type': 'application/x-amz-json-1.1'}
        )
        SigV4Auth(creds, 'bedrock', region).add_auth(request)

        import ssl
        ssl._create_default_https_context = ssl._create_unverified_context
        urllib_request = urllib.request.Request(
            url, data=request.body, headers=dict(request.headers), method='POST',
        )
        urllib.request.urlopen(urllib_request)
        print(f"✓ Entitlement successful for {model_id}")

if __name__ == "__main__":

    if len(sys.argv) < 2:
        print("Usage: python3 bedrock_access.py <model_id> [model_id2] ...")
        print("Example: python3 bedrock_access.py anthropic.claude-3-haiku-20240307-v1:0")
        sys.exit(1)

    models = sys.argv[1:]
    enable_bedrock_model_access(models)