from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from confluent_kafka import Producer
import json
import random
import logging
import boto3
from faker import Faker

fake = Faker()
logger = logging.getLogger(__name__)

#
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


def create_kafka_producer(config):
    """Create Kafka producer with configuration."""
    return Producer(config)


def generate_log():
    """Generate a synthetic log entry."""
    methods = ["GET", "POST", "PUT", "DELETE"]
    endpoints = ["/api/users", "/home", "/about", "/contact", "/services"]
    statuses = [200, 301, 302, 400, 404, 500]
    user_agents = [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X)",
        "Mozilla/5.0 (X11; Linux x86_64)",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    ]
    referrers = ["https://www.example.com", "https://www.google.com", "-"]

    ip = fake.ipv4()
    timestamp = datetime.now().strftime("%b %d %Y, %H:%M:%S")
    method = random.choice(methods)
    endpoint = random.choice(endpoints)
    status = random.choice(statuses)
    size = random.randint(1000, 15000)
    referrer = random.choice(referrers)
    user_agent = random.choice(user_agents)

    log_entry = (
        f"{ip} - - [{timestamp}] \"{method} {endpoint} HTTP/1.1\" {status} {size} "
        f"\"{referrer}\" \"{user_agent}\""
    )
    return log_entry


def delivery_report(err, msg):
    """Called once for each message produced to indicate delivery result."""
    if err is not None:
        logger.error(f"Message delivery failed: {err}")
    else:
        logger.info(f"Message delivered to {msg.topic()} [{msg.partition()}]")

def produce_logs(**context):
    """Produce log entries to Kafka."""
    secrets = get_secret("MWAA_Secrets_V2")
    kafka_config = {
        "bootstrap.servers": secrets['KAFKA_BOOTSTRAP_SERVER'],
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "PLAIN",
        "sasl.username": secrets['KAFKA_SASL_USERNAME'],
        "sasl.password": secrets['KAFKA_SASL_PASSWORD'],
        'session.timeout.ms': 50000
    }

    producer = create_kafka_producer(kafka_config)
    topic = 'billion_website_logs'

    for _ in range(1000):
        log = generate_log()
        try:
            producer.produce(topic, log.encode('utf-8'), on_delivery=delivery_report)
            producer.flush()
        except Exception as e:
            logger.error(f"Failed to produce log: {e}")

    logger.info(f"Produced 1,000 logs to topic {topic}")



# DAG Configuration
default_args = {
    'owner': 'nifesimii',
    'depends_on_past': False,
    'email_on_failure': False,
    'retries': 1,
    'retry_delay': timedelta(seconds=5),
}

dag = DAG(
    'log_generation_pipeline',
    default_args=default_args,
    description='Generate and produce synthetic logs',
    schedule_interval='*/2 * * * *',  # fixed from schedule -> schedule_interval
    start_date=datetime(2025, 1, 26),
    catchup=False,
    tags=['logs', 'kafka', 'production']
)

produce_logs_task = PythonOperator(
    task_id='generate_and_produce_logs',
    python_callable=produce_logs,
    dag=dag,
)
produce_logs()