"""Spark Structured Streaming enrichment job.

Consumes ``transactions.raw``, validates and deduplicates, joins the customer,
merchant and device dimensions, and writes ``transactions.enriched`` plus a
Parquet copy for the warehouse.

This is the horizontally scalable form of the enrichment stage that
``app/services/pipeline.py`` performs inline. At the demo volumes the inline
path is faster than Spark's fixed overhead; at 5k transactions per second this
job is what carries the load, and the API scorer consumes the enriched topic.

Run:
    spark-submit --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1 \
        infra/spark/stream_enrichment.py \
        --brokers kafka:9092 --jdbc jdbc:postgresql://postgres:5432/finguard
"""

from __future__ import annotations

import argparse

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# The envelope written by app/events/schemas.py.
PAYLOAD_SCHEMA = StructType(
    [
        StructField("transaction_id", StringType()),
        StructField("customer_id", StringType()),
        StructField("merchant_id", StringType()),
        StructField("account_id", StringType()),
        StructField("device_id", StringType()),
        StructField("amount", DoubleType()),
        StructField("currency", StringType()),
        StructField("occurred_at", TimestampType()),
        StructField("payment_method", StringType()),
        StructField("merchant_category", StringType()),
        StructField("channel", StringType()),
        StructField("transaction_type", StringType()),
        StructField("ip_address", StringType()),
        StructField("latitude", DoubleType()),
        StructField("longitude", DoubleType()),
        StructField("country", StringType()),
        StructField("city", StringType()),
        StructField("session_id", StringType()),
        StructField("is_demo", BooleanType()),
    ]
)

ENVELOPE_SCHEMA = StructType(
    [
        StructField("event_id", StringType()),
        StructField("event_type", StringType()),
        StructField("topic", StringType()),
        StructField("schema_version", StringType()),
        StructField("occurred_at", TimestampType()),
        StructField("correlation_id", StringType()),
        StructField("producer", StringType()),
        StructField("payload", PAYLOAD_SCHEMA),
    ]
)


def build_session(app_name: str = "finguard-stream-enrichment") -> SparkSession:
    return (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.shuffle.partitions", "12")
        .config("spark.sql.streaming.stateStore.stateSchemaCheck", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )


def read_dimension(spark: SparkSession, jdbc_url: str, table: str, properties: dict[str, str]):
    """Dimensions are small and change slowly; broadcast them into the stream."""
    return F.broadcast(spark.read.jdbc(url=jdbc_url, table=table, properties=properties))


def main() -> None:
    parser = argparse.ArgumentParser(description="FINGuard Spark enrichment")
    parser.add_argument("--brokers", default="kafka:9092")
    parser.add_argument("--jdbc", default="jdbc:postgresql://postgres:5432/finguard")
    parser.add_argument("--db-user", default="finguard")
    parser.add_argument("--db-password", default="finguard")
    parser.add_argument("--checkpoint", default="/tmp/finguard/checkpoints/enrichment")
    parser.add_argument("--output", default="/tmp/finguard/warehouse/transactions_enriched")
    parser.add_argument("--watermark", default="10 minutes")
    args = parser.parse_args()

    spark = build_session()
    spark.sparkContext.setLogLevel("WARN")

    properties = {"user": args.db_user, "password": args.db_password, "driver": "org.postgresql.Driver"}
    customers = read_dimension(spark, args.jdbc, "customers", properties).select(
        F.col("id").alias("customer_id"),
        F.col("segment").alias("customer_segment"),
        F.col("country").alias("customer_country"),
        F.col("risk_score").alias("customer_risk_score"),
        F.col("avg_transaction_amount"),
        F.col("watchlisted"),
    )
    merchants = read_dimension(spark, args.jdbc, "merchants", properties).select(
        F.col("id").alias("merchant_id"),
        F.col("category").alias("merchant_category_ref"),
        F.col("fraud_rate").alias("merchant_fraud_rate"),
        F.col("risk_score").alias("merchant_risk_score"),
        F.col("high_risk_flag"),
    )

    raw = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", args.brokers)
        .option("subscribe", "transactions.raw")
        .option("startingOffsets", "latest")
        .option("maxOffsetsPerTrigger", 50_000)
        .option("failOnDataLoss", "false")
        .load()
    )

    parsed = (
        raw.select(F.from_json(F.col("value").cast("string"), ENVELOPE_SCHEMA).alias("envelope"))
        .select("envelope.event_id", "envelope.correlation_id", "envelope.payload.*")
        .withColumn("ingested_at", F.current_timestamp())
    )

    # ---- validation ---------------------------------------------------------
    validated = parsed.filter(
        F.col("transaction_id").isNotNull()
        & F.col("customer_id").isNotNull()
        & F.col("merchant_id").isNotNull()
        & (F.col("amount") > 0)
        & (F.col("amount") < 1e12)
        & (F.length(F.col("currency")) == 3)
        & (F.col("latitude").isNull() | F.col("latitude").between(-90, 90))
        & (F.col("longitude").isNull() | F.col("longitude").between(-180, 180))
    )

    # ---- deduplication ------------------------------------------------------
    # The watermark bounds the state store: a replayed event older than the
    # watermark is dropped by the downstream idempotency ledger instead.
    deduplicated = validated.withWatermark("occurred_at", args.watermark).dropDuplicates(
        ["event_id"]
    )

    # ---- enrichment ---------------------------------------------------------
    enriched = (
        deduplicated.join(customers, on="customer_id", how="left")
        .join(merchants, on="merchant_id", how="left")
        .withColumn(
            "amount_ratio_to_avg",
            F.when(
                F.col("avg_transaction_amount") > 0,
                F.col("amount") / F.col("avg_transaction_amount"),
            ).otherwise(F.lit(1.0)),
        )
        .withColumn("is_cross_border", F.col("country") != F.col("customer_country"))
        .withColumn("hour_of_day", F.hour("occurred_at"))
        .withColumn("day_of_week", F.dayofweek("occurred_at"))
        .withColumn("is_night", F.col("hour_of_day") < 6)
        .withColumn("enriched_at", F.current_timestamp())
    )

    # ---- sink 1: the enriched Kafka topic the scorer consumes ---------------
    to_kafka = (
        enriched.select(
            F.col("customer_id").alias("key"),
            F.to_json(F.struct([F.col(c) for c in enriched.columns])).alias("value"),
        )
        .writeStream.format("kafka")
        .option("kafka.bootstrap.servers", args.brokers)
        .option("topic", "transactions.enriched")
        .option("checkpointLocation", f"{args.checkpoint}/kafka")
        .outputMode("append")
        .trigger(processingTime="5 seconds")
        .start()
    )

    # ---- sink 2: partitioned Parquet for the warehouse ----------------------
    to_parquet = (
        enriched.withColumn("event_date", F.to_date("occurred_at"))
        .writeStream.format("parquet")
        .option("path", args.output)
        .option("checkpointLocation", f"{args.checkpoint}/parquet")
        .partitionBy("event_date")
        .outputMode("append")
        .trigger(processingTime="60 seconds")
        .start()
    )

    to_kafka.awaitTermination()
    to_parquet.awaitTermination()


if __name__ == "__main__":
    main()
