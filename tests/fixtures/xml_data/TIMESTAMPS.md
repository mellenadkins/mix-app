# Timestamp-Based Modifications

## Background

- Adjusting a track's volume at a specific timestamp requires a programmatic script to modify the XML structure inside the unzipped .als file.

- Volume changes are handled via Automation Envelopes rather than static text labels.

## How Ableton Handles Volume Internally in XML:

- When you adjust a track's volume at a specific timestamp, Ableton does not store it as a simple "Gain = +1dB at 00:30:00" string. Instead, it uses a timeline envelope system:

1. Locating the Track: Inside the XML, you look for the <Tracks> node, find the specific track where Name or UserName matches "DRUMS".

2. Finding the Mixer Device: Inside that track's device chain, Ableton houses a <Volume> parameter node inside the track's mixer section.

3. Automation Breakpoints (<Automation>): Volume changes are mapped using breakpoints (nodes containing a time coordinate in beats and a floating-point value representing the fader position).

## Example:

To increase a track's volume by 1.0 dB at 00:30:00, a script has to perform specific steps:

### Step 1: Convert Timestamp to Musical Time (Beats)
XML files store placement natively in beats (musical time), not hours/minutes/seconds. Your script must:

Read the project's Tempo Map (e.g., 120 BPM, time signature 4/4).

Convert 00:30:00 (30 seconds) into the exact beat position. At 120 BPM, 30 seconds equals 60 beats.

### Step 2: Calculate the Fader Value Scaling
Ableton’s volume parameter doesn't use linear decibels directly in the XML; it uses a mapped scalar value (typically ranging from 0.0 to 1.0+, where standard unity gain 0 dB sits around a specific decimal point, and values scale non-linearly). Your script needs to convert a +1.0 dB delta into Ableton's internal float scale.

### Step 3: Inject or Modify Envelope Breakpoints
The script updates the XML's <Events> node under the volume automation envelope by inserting breakpoint XML tags:

<Events>
  <!-- Existing or new automation point near your timestamp -->
  <FloatEvent Time="3600" Value="0.8543..." />
</Events>