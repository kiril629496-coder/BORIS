from sqlalchemy import (MetaData,Table,Column,Integer,BigInteger,String,Text,DateTime,Boolean,JSON,Float,UniqueConstraint,Index,func)

metadata=MetaData()

profiles=Table('lead_radar_profiles',metadata,
 Column('id',BigInteger().with_variant(Integer,'sqlite'),primary_key=True,autoincrement=True),
 Column('owner_user_id',BigInteger,nullable=False,unique=True,index=True),
 Column('name',String(250),nullable=False),
 Column('business_type',String(30),nullable=False,default='services'),
 Column('mode',String(40),nullable=False,default='generic'),
 Column('geography_json',JSON,nullable=False,default=list),
 Column('intent_keywords',JSON,nullable=False,default=list),
 Column('negative_keywords',JSON,nullable=False,default=list),
 Column('min_score',Integer,nullable=False,default=45),
 Column('currency',String(10),nullable=False,default='RUB'),
 Column('enabled',Boolean,nullable=False,default=True),
 Column('settings_json',JSON,nullable=False,default=dict),
 Column('created_at',DateTime(timezone=True),server_default=func.now()),
 Column('updated_at',DateTime(timezone=True),server_default=func.now(),onupdate=func.now()))

offerings=Table('lead_radar_offerings',metadata,
 Column('id',BigInteger().with_variant(Integer,'sqlite'),primary_key=True,autoincrement=True),
 Column('owner_user_id',BigInteger,nullable=False,index=True),
 Column('offering_code',Integer,nullable=False),
 Column('external_key',String(120),nullable=False),
 Column('kind',String(20),nullable=False,default='service'),
 Column('name',String(500),nullable=False),
 Column('keywords',JSON,nullable=False,default=list),
 Column('negative_keywords',JSON,nullable=False,default=list),
 Column('price_min_rub',BigInteger),
 Column('price_max_rub',BigInteger),
 Column('currency',String(10),nullable=False,default='RUB'),
 Column('enabled',Boolean,nullable=False,default=True),
 Column('metadata_json',JSON,nullable=False,default=dict),
 Column('created_at',DateTime(timezone=True),server_default=func.now()),
 Column('updated_at',DateTime(timezone=True),server_default=func.now(),onupdate=func.now()),
 UniqueConstraint('owner_user_id','offering_code',name='uq_lead_radar_offering_owner_code'),
 UniqueConstraint('owner_user_id','external_key',name='uq_lead_radar_offering_owner_key'))
sources=Table('lead_radar_sources',metadata,
 Column('id',BigInteger().with_variant(Integer,'sqlite'),primary_key=True,autoincrement=True), Column('owner_user_id',BigInteger,nullable=False,index=True), Column('key',String(100),nullable=False),
 Column('family',String(40),nullable=False), Column('enabled',Boolean,nullable=False,default=True),
 Column('policy_json',JSON,nullable=False,default=dict), Column('last_scan_at',DateTime(timezone=True)),
 Column('last_success_at',DateTime(timezone=True)), Column('last_error',Text), Column('consecutive_failures',Integer,nullable=False,default=0),
 Column('next_scan_at',DateTime(timezone=True)), Column('last_failure_at',DateTime(timezone=True)),
 Column('recovery_strategy',String(80)), Column('blocked_external',Boolean,nullable=False,default=False),
 Column('created_at',DateTime(timezone=True),server_default=func.now()),
 Column('updated_at',DateTime(timezone=True),server_default=func.now(),onupdate=func.now()),
 UniqueConstraint('owner_user_id','key',name='uq_lead_radar_source_owner_key'))

leads=Table('lead_radar_leads',metadata,
 Column('id',BigInteger().with_variant(Integer,'sqlite'),primary_key=True,autoincrement=True), Column('owner_user_id',BigInteger,nullable=False,index=True),
 Column('fingerprint',String(64),nullable=False), Column('dedupe_key',String(64),nullable=False,index=True),
 Column('source_key',String(100),nullable=False,index=True), Column('external_id',String(300)), Column('url',Text),
 Column('title',Text,nullable=False), Column('body',Text), Column('author',Text), Column('company_name',Text), Column('company_domain',Text),
 Column('contact_hint',Text), Column('location',Text), Column('published_at',DateTime(timezone=True),nullable=False),
 Column('discovered_at',DateTime(timezone=True),nullable=False,server_default=func.now()), Column('signal_family',String(40),nullable=False),
 Column('service_ids',JSON,nullable=False,default=list), Column('score',Integer,nullable=False,default=0), Column('reasons',JSON,nullable=False,default=list),
 Column('budget_min_rub',BigInteger), Column('budget_max_rub',BigInteger), Column('urgency',String(20)), Column('decision_maker_hint',Text),
 Column('status',String(40),nullable=False,index=True), Column('contact_mode',String(30)), Column('contact_reason',Text), Column('reply_text',Text),
 Column('crm_deal_id',BigInteger), Column('first_contact_at',DateTime(timezone=True)), Column('last_contact_at',DateTime(timezone=True)),
 Column('next_followup_at',DateTime(timezone=True),index=True), Column('metadata_json',JSON,nullable=False,default=dict),
 Column('quality_version',String(64),nullable=False,default=''),
 Column('created_at',DateTime(timezone=True),server_default=func.now()), Column('updated_at',DateTime(timezone=True),server_default=func.now(),onupdate=func.now()),
 UniqueConstraint('owner_user_id','fingerprint',name='uq_lead_radar_lead_owner_fingerprint'))
Index('ix_lead_radar_dedupe_recent',leads.c.owner_user_id,leads.c.dedupe_key,leads.c.published_at)

events=Table('lead_radar_events',metadata,
 Column('id',BigInteger().with_variant(Integer,'sqlite'),primary_key=True,autoincrement=True), Column('lead_id',BigInteger,nullable=False,index=True),
 Column('event',String(80),nullable=False), Column('payload_json',JSON,nullable=False,default=dict),
 Column('created_at',DateTime(timezone=True),server_default=func.now()))

outreach=Table('lead_radar_outreach_attempts',metadata,
 Column('id',BigInteger().with_variant(Integer,'sqlite'),primary_key=True,autoincrement=True), Column('lead_id',BigInteger,nullable=False,index=True),
 Column('channel',String(80)), Column('mode',String(30),nullable=False), Column('idempotency_key',String(100),nullable=False,unique=True),
 Column('status',String(40),nullable=False), Column('attempt',Integer,nullable=False,default=1), Column('message_text',Text),
 Column('external_message_id',String(300)), Column('error',Text), Column('next_attempt_at',DateTime(timezone=True)),
 Column('created_at',DateTime(timezone=True),server_default=func.now()), Column('completed_at',DateTime(timezone=True)))
