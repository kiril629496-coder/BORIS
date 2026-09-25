from .models import SourcePolicy

# OPEN_WEB_PROVIDER_RATE_GUARD_V1
# 20 групп источников пользователя. По умолчанию fail-closed: AUTO только если конкретный adapter explicitly разрешён.
DEFAULT_SOURCE_POLICIES={
 'freelance':SourcePolicy('freelance','brief',True,False,False,60,1),
 'tenders':SourcePolicy('tenders','tender',False,False,False,60,0),
 'jobs':SourcePolicy('jobs','jobs',False,False,True,60,0),
 'startups':SourcePolicy('startups','startup',False,False,False,60,0),
 'telegram':SourcePolicy('telegram','social',True,False,False,60,1),
 # Public-search LinkedIn discovery is intentionally slow/read-only.
 'linkedin':SourcePolicy('linkedin','social',False,False,False,900,0),
 'communities':SourcePolicy('communities','social',False,False,False,60,0),
 'apps':SourcePolicy('apps','app_review',False,False,False,60,0),
  'maps':SourcePolicy('maps','audit',False,False,False,1800,0),
 'company_sites':SourcePolicy('company_sites','audit',False,False,False,60,0),
 'crm_companies':SourcePolicy('crm_companies','audit',False,False,False,60,0),
  'seo_agencies':SourcePolicy('seo_agencies','partner',False,False,False,1800,0),
  'design_studios':SourcePolicy('design_studios','partner',False,False,False,1800,0),
 'developers':SourcePolicy('developers','partner',False,False,False,60,0),
  'new_companies':SourcePolicy('new_companies','audit',False,False,False,900,0),
  'franchises':SourcePolicy('franchises','audit',False,False,False,1800,0),
  'news':SourcePolicy('news','money',False,False,False,300,0),
 'social':SourcePolicy('social','social',True,False,False,900,1),
  'reviews':SourcePolicy('reviews','reviews',False,False,False,1800,0),
 'cold_b2b':SourcePolicy('cold_b2b','audit',False,False,False,60,0),
 'hacker_news':SourcePolicy('hacker_news','social',True,False,False,60,0),
 'hacker_news_show':SourcePolicy('hacker_news_show','startup',False,False,False,1800,0),
 'hacker_news_jobs':SourcePolicy('hacker_news_jobs','jobs',False,False,True,1800,0),
 'apple_app_store':SourcePolicy('apple_app_store','app_review',False,False,False,300,0),
 'eu_ted':SourcePolicy('eu_ted','tender',False,False,False,60,0),
 'github_issues':SourcePolicy('github_issues','partner',False,False,False,1800,0),
 'freelancer':SourcePolicy('freelancer','brief',True,False,False,300,0),
 'boris_telegram':SourcePolicy('boris_telegram','social',False,False,False,300,0),
 'boris_crm_companies':SourcePolicy('boris_crm_companies','audit',False,False,False,1800,0),
 'boris_company_sites':SourcePolicy('boris_company_sites','audit',False,False,False,1800,0),
 'boris_cold_b2b':SourcePolicy('boris_cold_b2b','audit',False,False,False,3600,0),

}
