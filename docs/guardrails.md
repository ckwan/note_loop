# Guardrails

Rule based checks on every AI draft and review. Back to the [README](../README.md).

Rule based checks run on every AI draft and every review, whatever model wrote the text (`backend/app/guardrails.py`):

* **Grounding.** A diagnosis, medication, dose, or quote that appears in the note but not in the session notes is flagged. Drafts show a warning banner, and the review raises the same finding even when the model reviewer misses it.
* **Safety mention.** If the session notes mention risk of harm and the note never does, it is flagged.
* **Prompt injection.** Session notes are untrusted text. They sit inside tags the prompt marks as data, text that tries to close those tags is neutralized, and instruction like phrases such as "ignore previous instructions" raise a warning.
* **Input limits.** Notes are 20 to 8000 characters, and control characters are removed.

The audit log records the section and kind of each flag and never the message text. These checks are plain rules and catch common failures. They are not a clinical safety control, and a therapist still reviews and signs every note.
