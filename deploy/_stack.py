import boto3


def stack_outputs(stack, region=None):
    cfn = boto3.client("cloudformation", region_name=region)
    outputs = cfn.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}
