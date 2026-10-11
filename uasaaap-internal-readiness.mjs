#!/usr/bin/env node
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {securityLaunchDbReport} from 'file:///srv/uasaaap/current/server/marketplace/security-launch.js';

const allowedExact=new Set([
  'EXPOSED_SECRET_ROTATED',
  'BACKUP_OFFSITE',
  'SOURCE_REMOTE_PARITY',
  'ANDROID_PHYSICAL',
  'IPHONE_PHYSICAL',
  'PUSH_PHYSICAL',
  'PAYMENT_PROVIDER_LIVE',
  'CARD_BINDING_LIVE',
  'IDENTITY_VERIFICATION_LIVE',
  'BUSINESS_VERIFICATION_LIVE',
  'SMS_PRODUCTION',
  'PAYMENTS_ENABLED',
  'PAYMENT_PROVIDER',
  'PAYMENT_ENV=production',
  'PAYMENT_PROVIDER_API_KEY',
  'PAYMENT_PROVIDER_BASE_URL_HTTPS',
  'PAYMENT_PROVIDER_SHOP_ID',
  'IDENTITY_PROVIDER',
  'IDENTITY_CLIENT_ID',
  'IDENTITY_CLIENT_SECRET',
  'SBER_ID_CERT_PATH',
  'SBER_ID_KEY_PATH',
  'VTB_ID_CRYPTO_READY',
  'BUSINESS_VERIFICATION_PROVIDER',
  'BUSINESS_ID_CLIENT_ID',
  'BUSINESS_ID_CLIENT_SECRET',
  'SBER_BUSINESS_CERT_PATH',
  'SBER_BUSINESS_KEY_PATH',
  'IDENTITY_MANUAL_REVIEW_EVIDENCE',
  'BUSINESS_MANUAL_REVIEW_EVIDENCE'
]);
const externallyBound=name=>allowedExact.has(name)||name.startsWith('ALERT_CHANNELS>=2');

const db=new DatabaseSync('/srv/uasaaap/shared/data/marketplace.sqlite',{readOnly:true});
try{
  const report=securityLaunchDbReport(db,{...process.env,DEMO_MODE:'false'});
  const unexpected=report.failures.filter(name=>!externallyBound(name));
  const external=report.failures.filter(externallyBound);
  const out={
    status:unexpected.length?'FAIL':'PASS_EXTERNAL_ONLY',
    release:readFileSync('/srv/uasaaap/current/RELEASE_SHA','utf8').trim(),
    unexpected_internal:unexpected,
    external_or_physical_count:external.length,
    legal_required_active:report.legal_required_active,
    owner_active:report.owner_active,
    mfa_enrolled:report.mfa_enrolled,
    recovery_path:report.recovery_path,
    same_incident:!!report.same_incident_id
  };
  console.log(JSON.stringify(out));
  if(unexpected.length)process.exit(1);
}finally{db.close()}
