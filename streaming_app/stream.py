from __future__ import print_function
import json
import sys
import os

import boto3
from fleece.xray import monkey_patch_botocore_for_xray
monkey_patch_botocore_for_xray()

import twitter


SSM_NAME = os.getenv("SSM_PARAMETER_NAME")
REGION = os.getenv("REGION")
STREAM_NAME = os.getenv("STREAM_NAME")

session = boto3.session.Session(region_name=REGION)
ssm = session.client('ssm')
kinesis = session.client('kinesis')
api = twitter.Api(*ssm.get_parameters(Names=[SSM_NAME])['Parameters'][0]['Value'].split(','))
user = api.VerifyCredentials()
TWITTER_SN = user.screen_name


# We'll grab every single tweet stream item and put it into
if __name__ == '__main__':
    try:
        for update in api.GetUserStream():
            kinesis.put_record(
                StreamName=STREAM_NAME,
                PartitionKey=TWITTER_SN,
                Data=json.dumps(update)
            )
    except Exception as ex:
        print(ex, file=sys.stderr)
