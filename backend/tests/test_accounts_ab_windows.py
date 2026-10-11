from datetime import date, timedelta

from app.api.accounts import _ab_windows_aggregate


def snap(campaign_item_id, avito_id, views, contacts):
    return {
        "items": [{
            "id": avito_id,
            "views": views,
            "contacts": contacts,
            "campaign_identity": {
                "campaign_item_id": campaign_item_id,
                "avito_item_id": avito_id,
            },
        }]
    }


def test_ab_windows_exact_7_30_60_boundaries():
    today=date(2026,10,7)
    snapshots=[
        (today, snap(101,"a1",10,2)),
        (today-timedelta(days=6), snap(101,"a1",7,1)),
        (today-timedelta(days=7), snap(101,"a1",5,1)),
        (today-timedelta(days=29), snap(101,"a1",3,1)),
        (today-timedelta(days=59), snap(101,"a1",2,0)),
        (today-timedelta(days=60), snap(101,"a1",100,100)),
    ]
    out=_ab_windows_aggregate(snapshots,today)

    assert set(out)=={"7","30","60"}
    assert out["7"]["totals"]=={"views":17,"contacts":3,"conversion":17.65}
    assert out["30"]["totals"]=={"views":25,"contacts":5,"conversion":20.0}
    assert out["60"]["totals"]=={"views":27,"contacts":5,"conversion":18.52}
    assert out["7"]["by_campaign_item"]["101"]=={"views":17,"contacts":3,"conversion":17.65}
    assert out["30"]["by_avito_item"]["a1"]=={"views":25,"contacts":5,"conversion":20.0}
    assert out["60"]["days_with_data"]==5


def test_ab_windows_keeps_campaign_and_avito_identity_separate():
    today=date(2026,10,7)
    out=_ab_windows_aggregate([
        (today,snap(201,"av1",8,1)),
        (today,snap(202,"av2",4,2)),
    ],today)
    assert out["7"]["by_campaign_item"]["201"]["views"]==8
    assert out["7"]["by_campaign_item"]["202"]["contacts"]==2
    assert out["7"]["by_avito_item"]["av1"]["contacts"]==1
    assert out["7"]["totals"]["views"]==12
    assert out["7"]["totals"]["contacts"]==3
