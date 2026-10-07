"""Synthetic seed data. Run with: python -m app.seed"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .db import Base, SessionLocal, engine
from .models import Patient, TherapySession, Therapist


def seed(db: Session) -> None:
    if db.query(Therapist).first():
        return

    therapist = Therapist(name="Dr. Alex Rivera", email="alex.rivera@example.com")
    db.add(therapist)
    db.flush()

    patients = [
        Patient(therapist_id=therapist.id, display_name=name)
        for name in ("Jordan Test", "Sam Example", "Casey Sample")
    ]
    db.add_all(patients)
    db.flush()

    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for i in range(6):
        db.add(
            TherapySession(
                therapist_id=therapist.id,
                patient_id=patients[i % len(patients)].id,
                scheduled_at=start + timedelta(days=i // 3, hours=i % 3 + 1),
            )
        )
    db.commit()


if __name__ == "__main__":
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        seed(session)
    print("Seeded synthetic data.")
