from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator  # type: ignore
from confluent_kafka import Consumer, KafkaException
from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk
import json
import logging
import boto3
import re

logger = logging.getLogger(__name__)


def get_secret(secret_name, region_name="us-east-1"):
    """Retrieve secrets from AWS Secrets Manager."""
    session = boto3.session.Session()
    client = session.client(service_name='secretsmanager', region_name=region_name)
    try:
        response = client.get_secret_value(SecretId=secret_name)
        return json.loads(response['SecretString'])
    except Exception as e:
        logger.error(f"Secret retrieval error: {e}")
        raise


def parse_log_entry(log_entry):
    """Parse Apache log entry into structured data."""
    log_pattern = r'(?P<ip>[\d\.]+) - - \[(?P<timestamp>.*?)\] "(?P<method>\w+) (?P<endpoint>[\w/]+) (?P<protocol>[\w/\.]+)" (?P<status>\d+) (?P<size>\d+) "(?P<referrer>.*?)" "(?P<user_agent>.*?)"'

    match = re.match(log_pattern, log_entry)
    if not match:
        logger.warning(f"Invalid log format: {log_entry}")
        return None

    data = match.groupdict()

    try:
        parsed_timestamp = datetime.strptime(data['timestamp'], "%b %d %Y, %H:%M:%S")
        data['@timestamp'] = parsed_timestamp.isoformat()
    except ValueError:
        logger.error(f"Timestamp parsing error: {data['timestamp']}")
        return None

    return data


def consume_and_index_logs(**context):
    """Consume logs from Kafka and index to Elasticsearch."""
    secrets = get_secret("MWAA_Secrets_V1")

    # Kafka consumer configuration
    consumer_config = {
        "bootstrap.servers": secrets['KAFKA_BOOTSTRAP_SERVER'],
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "PLAIN",
        "sasl.username": secrets['KAFKA_SASL_USERNAME'],
        "sasl.password": secrets['KAFKA_SASL_PASSWORD'],
        "group.id": "airflow_log_indexer",
        "auto.offset.reset": "latest"  # Updated to process only latest messages
    }

    # Elasticsearch configuration
    es_config = {
        "hosts": [secrets['ELASTICSEARCH_URL']],
        "api_key": secrets['ELASTICSEARCH_API_KEY']
    }

    consumer = Consumer(consumer_config)
    es = Elasticsearch(**es_config)
    topic = 'billion_website_logs'
    consumer.subscribe([topic])

    try:
        # Ensure data stream exists
        data_stream_name = "billion_website_logs"
        if not es.indices.exists(index=data_stream_name):
            es.indices.create(index=data_stream_name)
            logger.info(f"Created data stream: {data_stream_name}")

        # Consume and index logs
        logs = []
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                break

            if msg.error():
                if msg.error().code() == KafkaException._PARTITION_EOF:
                    break
                raise KafkaException(msg.error())

            log_entry = msg.value().decode('utf-8')
            parsed_log = parse_log_entry(log_entry)

            if parsed_log:
                logs.append(parsed_log)

            # Index when 1,000 logs are collected
            if len(logs) >= 1000:
                actions = [
                    {
                        "_op_type": "create",
                        "_index": data_stream_name,
                        "_source": log
                    }
                    for log in logs
                ]

                try:
                    success, failed = bulk(es, actions, refresh=True)
                    logger.info(f"Indexed {success} logs, {len(failed)} failed")
                    logs = []
                except Exception as bulk_err:
                    logger.error(f"Bulk indexing error: {bulk_err}")

        # Index any remaining logs
        if logs:
            actions = [
                {
                    "_op_type": "create",
                    "_index": data_stream_name,
                    "_source": log
                }
                for log in logs
            ]
            bulk(es, actions, refresh=True)

    except Exception as e:
        logger.error(f"Log processing error: {e}")
    finally:
        consumer.close()
        es.close()


# DAG Configuration
# default_args = {
#     'owner': 'nifesimii',
#     'depends_on_past': False,
#     'email_on_failure': False,
#     'retries': 1,
#     'retry_delay': timedelta(seconds=5),
# }
#
# dag = DAG(
#     'log_consumer_pipeline',
#     default_args=default_args,
#     description='Consume logs from Kafka and index to Elasticsearch',
#     schedule='*/2 * * * *',
#     start_date=datetime(2024, 1, 25),
#     catchup=False,
#     tags=['logs', 'kafka', 'elasticsearch']
# )
#
# consume_logs_task = PythonOperator(
#     task_id='consume_and_index_logs',
#     python_callable=consume_and_index_logs,
#     dag=dag,
# )

# Define task dependencies
# consume_logs_task
consume_and_index_logs()
