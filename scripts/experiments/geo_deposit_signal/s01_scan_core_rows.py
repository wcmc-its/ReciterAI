# Read-only: Scans DynamoDB `reciterai` for PUB#/CORE# rows. Writes core_rows.json in cwd.
import sys, json, boto3
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[3]))
from utils.dynamodb_helpers import TABLE_NAME
c = boto3.client('dynamodb')
out=[]; kw=dict(TableName=TABLE_NAME, FilterExpression="begins_with(PK,:p) AND begins_with(SK,:s)",
  ExpressionAttributeValues={":p":{"S":"PUB#"},":s":{"S":"CORE#"}})
while True:
    r=c.scan(**kw)
    for it in r['Items']:
        d={k:list(v.values())[0] for k,v in it.items()}
        out.append(d)
    if 'LastEvaluatedKey' not in r: break
    kw['ExclusiveStartKey']=r['LastEvaluatedKey']
json.dump(out, open(sys.argv[1],'w'), default=str)
print(TABLE_NAME, len(out))
