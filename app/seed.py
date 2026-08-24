"""Demo-wedstrijd aanmaken om de flow zelf te kunnen doorlopen.

    uv run python -m app.seed --demo
"""

from __future__ import annotations

import argparse

from app.config import settings
from app.importer import create_competition, import_csv
from app.models import SessionLocal, create_all

DEMO_CSV = """naam,email,ronde,flight,starthole,marker
Jan de Vries,jan@example.nl,1,A,1,Piet Bakker
Piet Bakker,piet@example.nl,1,A,1,Jan de Vries
Anne Jansen,anne@example.nl,1,B,10,Kees Smit
Kees Smit,kees@example.nl,1,B,10,Anne Jansen
Jan de Vries,jan@example.nl,2,A,1,Anne Jansen
Anne Jansen,anne@example.nl,2,A,1,Jan de Vries
Piet Bakker,piet@example.nl,2,B,10,Kees Smit
Kees Smit,kees@example.nl,2,B,10,Piet Bakker
"""


def main() -> None:
    """Maak de demo-wedstrijd en print de links."""
    parser = argparse.ArgumentParser(description="Seed een demo-wedstrijd.")
    parser.add_argument("--demo", action="store_true", help="maak de demo-wedstrijd")
    args = parser.parse_args()
    if not args.demo:
        parser.error("gebruik --demo")

    create_all()
    with SessionLocal() as db:
        competition = create_competition(db, "Demo clubkampioenschap")
        result = import_csv(db, competition, DEMO_CSV)
        if not result.ok:
            for error in result.errors:
                print(f"FOUT: {error}")
            return

        print(f"\nCompetitie: {competition.name}")
        print(f"Leaderboard: {settings.base_url}/l/{competition.leaderboard_slug}\n")
        for name, round_no, token in result.new_links:
            print(f"Ronde {round_no}  {name:<16} {settings.base_url}/t/{token}")
        print(f"\nAdmin: {settings.base_url}/admin/login")


if __name__ == "__main__":
    main()
