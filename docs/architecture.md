# Mix Revision Agent — Architecture

**Status:** Phase 1 design document. No production code written yet.

---

## 1. Product goal (from CLAUDE.md)

A local Python application that:
1. Reads artist mix-revision emails (Gmail API, OAuth).
2. Translates explicit track-volume requests (dB, tied to instrument name) into structured changes via an LLM.
3. Validates all LLM output with Pydantic before execution.
4. Applies changes to an Ableton Live Set (.als) by editing `<Mixer><Volume><Manual Value>`.
5. Exports a new WAV from Ableton Live.
6. Replies to the artist by email with the WAV attached.

**Core safety principles (from CLAUDE.md):**
- Local-first. No session files or audio uploaded to external services.
- LLM interprets email only; LLM never modifies session files or sends email.
- Pydantic validation of all AI output before execution.
- Preserve original Ableton sessions and source WAVs.
- Every revision creates a new version from a known baseline.
- Every operation idempotent.
- Log all changes and errors.
- Never send a mix if validation or rendering fails.

---

## 2. High-level data flow

```
Gmail (incoming revision email)
    │
    ▼
[Email Ingest] ──────────────────────────────────┐
  - OAuth2 Gmail API                              │
  - Read unread messages in inbox                 │
  - Deduplicate by Message-ID (idempotency)       │
  - Extract plain-text body + thread ID           │
    │                                            │
    ▼                                            │
[LLM Interpretation] (Pydantic-validated)        │
  - System prompt: "extract track name + dB delta"│
  - Output: {track_name: str, volume_db: float}   │
  - Never touches session files                   │
    │                                            │
    ▼                                            │
[Validation & Safety]                            │
  - Clamp volume to [-70 dB, +6.02 dB]           │
  - Reject ambiguous/unknown tracks              │
  - Reject non-numeric or qualitative requests   │
    │                                            │
    ▼                                            │
[Session Manager]                                │
  - Load baseline .als (gzip + XML)              │
  - Find track by <UserName> / <EffectiveName>   │
  - Edit <Mixer><Volume><Manual Value>           │
  - Save as new versioned .als (never overwrite) │
    │                                            │
    ▼                                            │
[Render / Export]                                │
  - Initiate WAV export from Ableton Live        │
  - Monitor completion (file watcher)            │
  - Validate WAV exists + non-zero               │
    │                                            │
    ▼                                            │
[Email Reply]                                    │
  - Attach validated WAV                         │
  - Reply to original thread                     │
  - Only sent if all prior steps succeeded       │
```

---

## 3. Components

### 3.1 Email Ingest (`src/mix_revision_agent/email/`)
- **Interface:** `fetch_pending_revisions() -> list[RevisionEmail]`
- **Responsibilities:**
  - Gmail API OAuth2 (read-only scope + send scope for replies)
  - List unread messages, filter by subject/keyword (e.g., "mix revision")
  - Extract plain-text body, Message-ID, thread ID, sender
  - Deduplicate against processed-message store (idempotency)
- **External dependency:** Gmail API (network, external service — email only, not audio)
- **Safety:** Never sends email from this component; only reads.

### 3.2 LLM Interpretation (`src/mix_revision_agent/ai/`)
- **Interface:** `interpret_revision(text: str) -> RevisionRequest`
- **Pydantic model:**
  ```python
  class RevisionRequest(BaseModel):
      track_name: str          # instrument name, required
      volume_db: float         # dB delta, required
  ```
- **Responsibilities:**
  - System prompt instructs LLM to extract explicit numerical dB changes tied to instrument names
  - Reject EQ, compression, panning, effects, qualitative language ("make it warmer")
  - Output constrained to the Pydantic schema (function calling / structured output)
- **Safety:** LLM output is **never** executed directly — always validated by Pydantic and the Session Manager.

### 3.3 Validation & Safety (`src/mix_revision_agent/workflow/`)
- **Interface:** `validate_and_plan(request: RevisionRequest, baseline: AlsFile) -> EditPlan`
- **Responsibilities:**
  - Clamp `volume_db` to [-70, +6.02] dB; reject if out of bounds
  - Look up track by name in `.als`; reject unknown/ambiguous tracks
  - Compute target linear gain: `10^(target_db / 20)`
  - Produce an `EditPlan` (track identifier, old value, new value, new file path)
  - Idempotency check: skip if this revision was already applied
- **Safety:** Central gate — nothing reaches the `.als` editor without passing here.

### 3.4 Session Manager / .als Editor (`src/mix_revision_agent/ableton/`)
- **Interface:** `apply_edit(plan: EditPlan) -> Path` (returns path to new versioned `.als`)
- **Responsibilities:**
  - Decompress `.als` (gzip) → XML
  - Parse with `xml.etree.ElementTree`
  - Find track by `<UserName>` (preferred) / `<EffectiveName>` fallback
  - Locate `<DeviceChain><Mixer><Volume><Manual Value="..."/>`
  - Replace `Value` with clamped linear gain
  - Recompress to `.als`, save as `basename_rev{N}.als` (never overwrite original)
  - Verify: gunzip + parse the output to confirm well-formed XML
- **Safety:** Original `.als` is preserved; new version is written to a separate file.

### 3.5 Render / Export (`src/mix_revision_agent/render/`)
- **Interface:** `export_wav(als_path: Path) -> Path` (returns path to WAV)
- **Responsibilities:**
  - Initiate WAV export from Ableton Live (method TBD — see Section 6)
  - Monitor completion (file-watcher on export folder + size stabilization)
  - Validate WAV exists, non-zero size, valid RIFF header
  - Return WAV path or raise on failure
- **Safety:** If export fails or WAV is invalid, no email is sent.

### 3.6 Email Reply (`src/mix_revision_agent/email/`)
- **Interface:** `send_reply(thread_id, to, wav_path, summary: str)`
- **Responsibilities:**
  - Gmail API send, replying to the original thread
  - Attach the validated WAV
  - Body: plain-text summary of what was changed (track name, dB change)
- **Safety:** Only sent after WAV validation passes. Never sends without a WAV.

### 3.7 Orchestrator (`src/mix_revision_agent/workflow/`)
- **Interface:** `process_revision(email: RevisionEmail) -> Result`
- Chains: ingest → interpret → validate → edit → export → reply
- Logs every step and error; records processed Message-IDs for idempotency.

---

## 4. Data models

```
RevisionEmail
  message_id: str
  thread_id: str
  sender: str
  subject: str
  body_text: str
  received_at: datetime

RevisionRequest  (Pydantic, LLM output)
  track_name: str
  volume_db: float

EditPlan
  als_path: Path           # baseline
  track_name: str          # matched track
  track_index: int
  old_linear_gain: float
  new_linear_gain: float
  output_als_path: Path    # new versioned file

RevisionResult
  success: bool
  wav_path: Optional[Path]
  als_path: Optional[Path]
  error: Optional[str]
```

---

## 5. Ableton .als integration (from docs/ableton-format-notes.md)

**Verified facts:**
- `.als` = gzip-compressed XML
- Static mixer volume at `<DeviceChain><Mixer><Volume><Manual Value="linear"/>`
- Linear scale: `1.0 = 0 dB`; `linear = 10^(dB/20)`
- Hard bounds: [−70 dB, +6.02 dB]
- Track name in `<Name><UserName Value="..."/>` (preferred) / `<EffectiveName Value="..."/>` (fallback)
- Live 12 opens Live 11 files; not vice versa. MVP targets Live 12.
- No checksum complaints on simple value edits (correctly gzipped + well-formed XML is sufficient).
- Multiple version files from one baseline all reopen independently.

**Editing method (MVP): Direct `.als` XML editing.**
- Decompress → edit `<Manual Value>` → recompress → save as new versioned file.
- Rationale: simplest, no running Live instance required, verified to work by manual test.
- Risk: "invalid file structure" if value breaks structural logic — mitigated by clamping to verified bounds.

---

## 6. WAV export — critical architectural risk

**Finding (from research agent):** Ableton Live has **no native API** for triggering WAV export from outside the application. All approaches rely on GUI automation of the Export Audio/Video dialog:

| Option | Reliability | Notes |
|--------|-------------|-------|
| Human-triggered export + file watcher | Highest | Human clicks Export in Live; automation watches folder for WAV, waits for size stabilization |
| ableton-mcp-max `render_audio` | Medium | System Events / Accessibility automation; requires Accessibility permissions, output path not controllable |
| AppleScript / System Events | Medium | Same class as above, more brittle with Live's non-Cocoa windows |
| M4L dialog observation | Low | No "render finished" event; inference only |
| PyAutoGUI screen coordinates | Lowest | Most fragile; breaks on resolution/theme changes |

**Recommended MVP approach:** **Human-in-the-loop export + automated file-watch pickup.**
- The MVP notifies the user (or the artist) that a revised `.als` is ready.
- A human triggers WAV export in Ableton Live.
- The agent watches the export folder, detects the new WAV, waits for size stabilization, then proceeds to email reply.
- Programmatic export (ableton-mcp-max) is a future enhancement, not MVP.

**Unresolved:** Can WAV export be initiated and completion reliably detected without human interaction? Answer: **uncertain / technically possible but not reliable for production unattended use.**

---

## 7. Key assumptions

1. **Live 12** is the target version (majority of active users).
2. Each artist has **one Ableton project per email thread** (from CLAUDE.md MVP scope).
3. Revision emails contain **explicit numerical dB changes tied to instrument names** (no EQ, compression, panning, qualitative language).
4. Track names in emails match `<UserName>` or `<EffectiveName>` in the `.als`.
5. Static volume changes are sufficient; automation editing is out of scope.
6. WAV export requires human initiation in the MVP (see Section 6).
7. Original `.als` and source WAVs are never overwritten.

---

## 8. Integration boundaries (behind interfaces)

- Gmail API → `EmailPort` interface (read + send)
- LLM → `InterpreterPort` interface (interpret text → RevisionRequest)
- `.als` file → `SessionPort` interface (read track, apply volume edit, save version)
- WAV export → `RenderPort` interface (export .als → WAV path)
- Filesystem → `StoragePort` interface (read/write .als, WAV, processed-message log)

This keeps external integrations testable with mocks (unit tests for business logic without Gmail/Live/LLM).

---

## 9. Logging & observability

- Structured logging (JSON) for all operations: email received, LLM interpretation, validation result, .als edit, export status, email sent, errors.
- Each revision gets a correlation ID (Message-ID) traced through the pipeline.
- Failed operations log enough context to replay manually (email body, track name, dB request).

---

## 10. Test strategy

- **Unit tests:** Pydantic validation, dB↔linear conversion, track-name matching, clamping, idempotency logic. (All business logic, no external deps.)
- **Integration tests:** `.als` round-trip (decompress → edit → recompress → reparse) using test `.als` fixtures.
- **End-to-end:** Not automated in MVP (requires Gmail + Ableton Live + human export). Manual validation only.

---

## 11. Immediate next steps (before implementation)

1. Create test `.als` fixture files (or obtain real ones from the artist) for round-trip testing.
2. Verify `.als` round-trip editing works with Python `gzip` + `xml.etree.ElementTree` on a real Live 12 file.
3. Confirm track-name matching logic against real artist `.als` files (are `<UserName>` fields populated?).
4. Decide on WAV export MVP: human-in-the-loop vs. programmatic (ableton-mcp-max).
