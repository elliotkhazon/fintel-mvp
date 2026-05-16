"""Shared fixtures for integration tests — live AWS, run post-apply in staging/prod."""
import os
import boto3
import pytest

ENV = os.environ.get("FINTEL_ENV", "staging")
REGION = "us-east-1"


@pytest.fixture(scope="session")
def env():
    return ENV


@pytest.fixture(scope="session")
def s3_client():
    return boto3.client("s3", region_name=REGION)


@pytest.fixture(scope="session")
def sm_client():
    return boto3.client("secretsmanager", region_name=REGION)


@pytest.fixture(scope="session")
def ecr_client():
    return boto3.client("ecr", region_name=REGION)


@pytest.fixture(scope="session")
def ec2_client():
    return boto3.client("ec2", region_name=REGION)


@pytest.fixture(scope="session")
def ssm_client():
    return boto3.client("ssm", region_name=REGION)
