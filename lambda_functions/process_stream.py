from __future__ import print_function

import base64
import json
import io
import os
import time

import boto3
from fleece.xray import monkey_patch_botocore_for_xray
monkey_patch_botocore_for_xray()
from botocore.vendored import requests

from PIL import Image

import twitter

rekognition = boto3.client('rekognition')
s3 = boto3.resource('s3')
ddb = boto3.resource('dynamodb').Table(os.getenv("DDB_TABLE"))
ssm = boto3.client('ssm')
unprocessed_bucket = s3.Bucket(os.getenv("UNPROCESSED_BUCKET"))
processed_bucket = s3.Bucket(os.getenv("PROCESSED_BUCKET"))

api = twitter.Api(*ssm.get_parameters(Names=[os.getenv("SSM_PARAMETER_NAME")])['Parameters'][0]['Value'].split(','))
TWITTER_SN = api.VerifyCredentials().screen_name

MASK = Image.open("mask.png")

POSITIVE_STATUS = "#cloudninja {0}"
NEGATIVE_STATUS = "@{0} sorry I couldn't figure out how to ninjafy you :(!"
NSFW_STATUS = "@{0} sorry but that doesn't look SFW (sorry if I'm wrong)"


def is_nsfw(s3obj):
    try:
        resp = rekognition.detect_moderation_labels(Image=s3obj, MinConfidence=50.)
        for label in resp['ModerationLabels']:
            if 'Explicit Nudity' in [label['Name'], label['ParentName']]:
                return True
    except:
        return True
    return False


def get_faces(image):
    resp = rekognition.detect_faces(Image=image)
    if 'FaceDetails' in resp:
        return resp['FaceDetails']
    else:
        return []


def get_face_boxes(faces, source_size):
    # this list comprehension builds a bounding box around the faces
    return [
        (
            int(f['BoundingBox']['Left'] * source_size[0]),
            int(f['BoundingBox']['Top'] * source_size[1]),
            int((f['BoundingBox']['Left'] + f['BoundingBox']['Width']) * source_size[0]),
            int((f['BoundingBox']['Top'] + f['BoundingBox']['Height']) * source_size[1]),
            # we store the final coordinate of the bounding box as the pitch of the face
            f['Pose']['Roll']
        )
        for f in faces
    ]


def build_masked_image(source, mask, boxes):
    for box in boxes:
        size = (box[2] - box[0], box[3] - box[1])
        scaled_mask = mask.rotate(-box[4], expand=1).resize(size, Image.ANTIALIAS)
        # we cut off the final element of the box because it's the rotation
        source.paste(scaled_mask, box[:4], scaled_mask)


def ninjafy(source, mask):
    if isinstance(source, dict):
        faces = get_faces(source)
    else:
        raise ValueError("Need S3Object")
    data = io.BytesIO()
    s3.Object(source['S3Object']['Bucket'], source['S3Object']['Name']).download_fileobj(data)
    source_img = Image.open(data)
    boxes = get_face_boxes(faces, source_img.size)
    if boxes:
        build_masked_image(source_img, mask, boxes)
    else:
        return None
    return source_img


def validate_record(payload):
    if (
        TWITTER_SN in payload.get('text', '') and
        payload.get('entities', {}).get('media') and
        'RT' not in payload.get('text')
    ):
        return True
    return False


def process_record(payload):
    # construct ddb entry from data
    ddb_item = {
        'mid': str(payload['entities']['media'][0]['id']),
        'tid': payload['id'],
        'media': payload['entities']['media'][0]['media_url'],
        'text': payload['text'],
        'sn': payload['user']['screen_name'],
        'mentions': ['@'+mention['screen_name'] for mention in payload['entities']['user_mentions'] if TWITTER_SN[1:] not in mention['screen_name']],
        'ts': int(time.time())
    }
    # grab image from twitter
    resp = requests.get(ddb_item['media']+":large")
    # store unprocessed image in s3 bucket for rekognition to process
    unprocessed_bucket.put_object(Body=resp.content, Key=str(ddb_item['mid']), ACL='public-read')
    ddb.put_item(Item=ddb_item)


def lambda_handler(event, context):
    for record in event['Records']:
        payload = json.loads(base64.b64decode(record['kinesis']['data']))
        if not validate_record(payload):
            return
        process_record(payload)
        item = ddb.get_item(
            Key={'mid': str(payload['entities']['media'][0]['id'])},
            ConsistentRead=True
        )['Item']
        s3obj = {
            'S3Object': {
                'Bucket': os.getenv("UNPROCESSED_BUCKET"),
                'Name': str(payload['entities']['media'][0]['id'])
            }
        }
        if is_nsfw(s3obj):
            api.PostUpdate(NSFW_STATUS.format(item['sn']), in_reply_to_status_id=item['tid'])
            unprocessed_bucket.delete_objects(Delete={'Objects': [{'Key': str(item['mid'])}]})
            continue
        processed = ninjafy(s3obj, MASK)
        buf = io.BytesIO()
        if processed:
            s3_url = "{}/{}/{}".format(
                s3.meta.client.meta.endpoint_url,
                os.getenv("PROCESSED_BUCKET"),
                str(item['mid']) + ".jpg"
            )
            processed.save(buf, 'JPEG')
            processed_bucket.put_object(Body=buf.getvalue(), Key=str(item['mid'])+".jpg", ACL='public-read')
            mentions = item['mentions']
            mentions.append('@'+item['sn'])
            mentions_str = ' '.join(item['mentions'])
            api.PostUpdate(
                POSITIVE_STATUS.format(mentions_str),
                media=s3_url,
                in_reply_to_status_id=item['tid']
            )
            unprocessed_bucket.delete_objects(Delete={'Objects': [{'Key': str(item['mid'])}]})
        else:
            api.PostUpdate(NEGATIVE_STATUS.format(item['sn']), in_reply_to_status_id=item['tid'])
            unprocessed_bucket.delete_objects(Delete={'Objects': [{'Key': str(item['mid'])}]})
