"""De audit log hangt aan de wedstrijd, niet aan de wedstrijdleider.

Waar een regel over gaat staat in `competition_id`, wie hem veroorzaakte in `actor`. Dat
zijn twee vragen en die stonden vroeger in één kolom: `user_id` hield de eigenaar bij op
het moment van schrijven, en dus liep hij achter zodra een wedstrijd van eigenaar wisselde.
"""

from __future__ import annotations

import json

from sqlalchemy import inspect, select, text

from app.models import AuditLog, User, create_all, engine, now
from tests.conftest import WACHTWOORD_HASH
from tests.helpers import LEIDING, WACHTWOORD, entry_van, login_admin, vul_kaart


def kolommen(tabel: str) -> set[str]:
    """De kolomnamen die de database nu echt heeft."""
    return {kolom["name"] for kolom in inspect(engine).get_columns(tabel)}


def test_een_score_noemt_de_deelname_die_hem_invoerde(db, wedstrijd):
    """`entry:` en niet `player:`: het id is dat van de deelname, niet van de speler."""
    competition, _ = wedstrijd
    jan = entry_van(db, "Jan")
    vul_kaart(db, jan, holes=1)

    db.expire_all()
    regels = db.scalars(select(AuditLog).where(AuditLog.action == "score")).all()

    assert regels != []
    assert all(r.competition_id == competition.id for r in regels)
    assert {r.actor for r in regels} == {f"entry:{jan.id}", f"entry:{jan.marker.id}"}


def test_een_ingreep_noemt_het_account_van_de_wedstrijdleider(db, client, wedstrijd, gebruiker):
    """Niet 'admin': dat zegt niet wélke wedstrijdleider het was."""
    competition, _ = wedstrijd
    login_admin(client)
    jan = entry_van(db, "Jan")

    client.post(f"/admin/entry/{jan.id}/status", data={"status": "dq", "reden": "te laat"})

    db.expire_all()
    regel = db.scalar(select(AuditLog).where(AuditLog.action == "status"))
    assert regel.actor == f"user:{gebruiker.id}"
    assert regel.competition_id == competition.id


def test_elke_actor_draagt_zijn_tabel_bij_zich(db, client, wedstrijd):
    """Zonder voorvoegsel valt een id uit de ene tabel niet van dat uit de andere te
    onderscheiden, en wijst een regel dus de verkeerde persoon aan."""
    login_admin(client)
    jan = entry_van(db, "Jan")
    client.post(f"/admin/entry/{jan.id}/status", data={"status": "dq", "reden": "x"})
    vul_kaart(db, entry_van(db, "Jan"), holes=1)

    db.expire_all()
    actoren = {r.actor for r in db.scalars(select(AuditLog))}

    assert actoren != set()
    assert all(a.split(":")[0] in ("user", "entry") for a in actoren), actoren
    assert all(a.split(":")[1].isdigit() for a in actoren), actoren


def test_de_wedstrijd_weg_is_de_log_weg(db, wedstrijd):
    """Een regel over een wedstrijd die niet meer bestaat zegt niets meer."""
    competition, _ = wedstrijd
    vul_kaart(db, entry_van(db, "Jan"), holes=1)
    db.commit()
    assert db.scalar(
        select(AuditLog).where(AuditLog.competition_id == competition.id)
    ) is not None

    db.execute(text("delete from competition where id = :id"), {"id": competition.id})
    db.commit()

    db.expire_all()
    assert db.scalars(select(AuditLog)).all() == []


def test_de_geschiedenis_verhuist_mee_met_de_wedstrijd(db, client, wedstrijd, gebruiker):
    """Een wedstrijd overdragen neemt zijn log mee. Dat is het hele punt van de kolom."""
    competition, _ = wedstrijd
    ander = User(email="ander@club.nl", password_hash=WACHTWOORD_HASH, confirmed_at=now())
    db.add(ander)
    db.commit()

    competition.user_id = ander.id
    db.commit()

    login_admin(client, "ander@club.nl")
    assert "Testwedstrijd" in client.get("/admin/audit.csv").text

    login_admin(client, LEIDING, WACHTWOORD)
    oud = client.get("/admin/audit.csv").text.strip().splitlines()
    assert len(oud) == 1, "alleen de kop: hij heeft geen wedstrijd meer"


def test_een_aanmelding_hangt_aan_geen_enkele_wedstrijd(db, client):
    """Die gaat over de installatie zelf en hoort in geen enkele export."""
    client.post("/admin/registreren", data={"email": "nieuw@club.nl", "wachtwoord": WACHTWOORD})

    db.expire_all()
    regel = db.scalar(select(AuditLog).where(AuditLog.action == "registered"))
    nieuw = db.scalar(select(User).where(User.email == "nieuw@club.nl"))
    assert regel.competition_id is None
    assert regel.actor == f"user:{nieuw.id}"


def test_de_oude_log_verhuist_naar_de_wedstrijd(db, wedstrijd, gebruiker):
    """De database van voor deze wijziging, precies zoals hij in productie staat.

    Regels die naar een deelname of een wedstrijd wijzen zijn te plaatsen. Wat dat niet is
    ging over een wedstrijd die er niet meer is, en verdwijnt mee.
    """
    competition, _ = wedstrijd
    jan = entry_van(db, "Jan")
    db.execute(text("delete from audit_log"))
    db.execute(text("alter table audit_log drop column competition_id"))
    db.execute(
        text(
            "alter table audit_log add column user_id integer "
            "references app_user(id) on delete set null"
        )
    )
    db.execute(
        text(
            "insert into audit_log (at, actor, action, detail, user_id) values "
            "(now(), 'admin', 'verbergen', cast(:wed as json), :u),"
            "(now(), :speler, 'score', cast(:deelname as json), :u),"
            "(now(), 'admin', 'rotate', cast(:weg as json), :u),"
            "(now(), 'admin', 'wis_spelers', cast(:wed as json), null),"
            "(now(), 'account', 'confirmed', cast(:mail as json), null)"
        ),
        {
            "wed": json.dumps({"competition": competition.id}),
            "deelname": json.dumps({"entry": jan.id}),
            "weg": json.dumps({"entry": 999999}),
            "mail": json.dumps({"email": gebruiker.email}),
            "speler": f"player:{jan.id}",
            "u": gebruiker.id,
        },
    )
    db.commit()

    create_all()

    db.expire_all()
    regels = {r.action: r for r in db.scalars(select(AuditLog))}
    assert "rotate" not in regels, "wees naar een verdwenen deelname, dus naar geen wedstrijd"
    assert regels["verbergen"].competition_id == competition.id
    assert regels["verbergen"].actor == f"user:{gebruiker.id}"
    assert regels["score"].competition_id == competition.id
    assert regels["score"].actor == f"entry:{jan.id}", "player: noemde altijd al een deelname"
    assert regels["wis_spelers"].actor == f"user:{gebruiker.id}", (
        "van voor de accounts: geen user_id, dus de eigenaar van de wedstrijd"
    )
    assert regels["confirmed"].competition_id is None
    assert regels["confirmed"].actor == f"user:{gebruiker.id}"
    assert "user_id" not in kolommen("audit_log")
