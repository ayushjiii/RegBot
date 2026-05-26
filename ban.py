from ai_agent_db import get_db, BlacklistedDomain

bad_sites = [
    {"domain": "demoqa.com", "reason": "Too much React garbage"},
    {"domain": "compendiumdev.co.uk", "reason": "Empty DOM / Too fancy"}
]

with get_db() as db:
    for site in bad_sites:
        exists = db.query(BlacklistedDomain).filter_by(domain=site["domain"]).first()
        if not exists:
            db.add(BlacklistedDomain(domain=site["domain"], reason=site["reason"]))
            print(f"✅ Successfully banned: {site['domain']}")
        else:
            print(f"⚠️ Already banned: {site['domain']}")