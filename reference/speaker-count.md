# Counting who speaks, from screenshots

When the person answers `Não sei` in the form of step 4 of [`SKILL.md`](../SKILL.md), the skill does not
leave the count to pyannote alone: `prepare.py --speaker-shots` takes 12 screenshots spread over the video
(seconds; `cache/<slug>-<fp8>/shots-<h8>/`), and an agent reads them and answers with a range, which becomes
that video's `num_speakers`. The minimum and the maximum guide pyannote without forcing a single number.

**How to call it.** One Agent per video, all in the same message: `subagent_type: "general-purpose"`,
`model: "sonnet"`, `description: "Count speakers from screenshots"`, and as `prompt` the text between the two
lines below, with `<TITLE>`, `<DURATION>` and `<PATHS>` replaced: the video's `title`, `duration` and
`shots.paths` from the `prepare.py --speaker-shots` JSON, one path per line. You (the main agent) never open the
screenshots.

**How to read the answer.** Its last line is `SPEAKERS: <value>`, already in the grammar of `num_speakers`:
`4-12`, `3`, `4+` or `auto`. Write `v<N>=<value>` in the request. A reply without that line, or with another
value, counts as `auto`. The reply carries counts and screenshot numbers, never names.

---

Estimate how many different people speak in the recording «<TITLE>» (<DURATION>), using only the screenshots
listed below, taken at instants spread evenly over it. Your numbers become the minimum and maximum number of
speakers for a speaker-separation model, so a safe range is worth more than a single guess.

Open every screenshot with the Read tool (they are JPEG images). Open nothing else, do not write any file, and
do not run any command.

<PATHS>

In each screenshot, look for:
- the participant tiles or webcams, with a name or initials on each; a profile photo or an avatar with initials
  counts as a person;
- the people counter of the meeting app (Teams: "Pessoas N" / "People N" / "Participantes N"; Zoom:
  "Participants (N)"; Meet: the people icon with a number), and a "+N" badge for participants without a tile;
- the speaking indicator: a coloured border or glow around the tile of whoever is talking at that instant;
- "<name> is presenting" / "<nome> está apresentando", or the presenter's own tile;
- in a lecture, a class or a screen recording narrated by one person: a single presenter.

Then decide:
- min: the distinct people with direct evidence of speaking (the speaking indicator); at least 1 when someone
  presents or narrates;
- max: the distinct people visible or counted (tiles, the counter, the "+N" badge); someone never seen speaking
  still counts, since a screenshot cannot show that they stayed silent;
- when the screenshots show no participant at all (only slides, a shared screen without tiles, an empty room),
  answer `auto`.

Merge the same person seen in different layouts. Never write a name, initials, e-mail or any other personal
data in your reply, not even shortened: refer to people only as "person A", "person B"… and to screenshots by
number. Reply with at most three short lines (what you counted, by screenshot number; your confidence: low,
medium or high), then end with exactly one line in one of these forms:

SPEAKERS: <min>-<max>
SPEAKERS: <n>
SPEAKERS: auto

(`<n>` when the minimum equals the maximum.)

---
