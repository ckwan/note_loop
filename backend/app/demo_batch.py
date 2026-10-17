"""Create synthetic signed notes with failed claims, to try the batch resubmit.

Run with: python -m app.demo_batch 200
Synthetic data only. Safe to run more than once.
"""

import sys
from datetime import datetime, timedelta, timezone

from .db import Base, SessionLocal, engine
from .models import Claim, Note, Patient, TherapySession, Therapist
from .seed import seed

SOAP = {
    "subjective": "Synthetic demo note.",
    "objective": "Not documented.",
    "assessment": "Synthetic demo note.",
    "plan": "Not documented.",
}


def create_failed_claims(count: int) -> int:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
        therapist = db.query(Therapist).first()
        patient = db.query(Patient).first()
        start = datetime.now(timezone.utc) - timedelta(days=1)

        for i in range(count):
            session = TherapySession(
                therapist_id=therapist.id,
                patient_id=patient.id,
                scheduled_at=start + timedelta(minutes=i),
            )
            db.add(session)
            db.flush()

            note = Note(
                session_id=session.id,
                raw_input="Synthetic demo input for the batch resubmit example.",
                ai_draft=SOAP,
                final_note=SOAP,
                status="signed",
                signed_at=start,
            )
            db.add(note)
            db.flush()

            db.add(Claim(note_id=note.id, status="failed"))
        db.commit()
    return count


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    print(f"Created {create_failed_claims(n)} synthetic failed claims.")
