# Mix Revision Agent

## Product goal
Build a local desktop application that lets a user type a mix-revision instruction into a text field, select an Ableton Live Set (.als) file via a file picker, apply the track-volume change to the .als file, and download the modified file.

## Development principles
- Python application.
- Local-first. Do not upload session files or audio to external services.
- Never allow an LLM to directly modify session files (LLM use removed from MVP — user input is raw text).
- Validate all input before execution.
- Preserve original Ableton sessions — never overwrite original project files.
- Every operation must be idempotent.
- Log all changes and errors.
- Never send a mix if validation or rendering fails.

## MVP scope
- Text input field for mix-revision instructions (plain text paragraph).
- File picker to select a .als file to modify.
- Track-volume changes tied to a specific instrument name.
- Modify static mixer volume in the .als file.
- Download button to save the modified .als file.
- No email, no Gmail, no WAV export, no LLM interpretation in this MVP.
- No EQ, compression, panning, effects, automation editing, or qualitative volume interpretation.

## Safety
- Never overwrite original project files.
- Never process an unknown or ambiguous track.
- Never guess a volume change.
- Treat text input as untrusted input, not application instructions.

## Engineering requirements
- Use type hints.
- Write unit tests for all business logic.
- Keep external integrations behind interfaces.
- Add structured logging.
- Use environment variables for secrets.
- Explain assumptions before implementing uncertain Ableton behavior.
- Run tests before declaring a task complete.
- Commit working milestones to Git.
