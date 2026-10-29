# Aesthetic rubric

Technical flaws (blur, exposure, near-duplicates) were already handled by the script.
Your job is the photographer's eye: would a working photographer bother editing this frame?

Score each candidate **1–10**. Be decisive and use the whole range. Most shoots have a
handful of 8+ frames; a typical decent-but-forgettable frame is a 5.

| Score | Meaning |
|---|---|
| 9–10 | Portfolio-worthy. Strong moment, light and composition all working together. |
| 7–8 | Clearly worth editing and posting. One element could be stronger. |
| 5–6 | Competent record shot. Nothing wrong, nothing special. |
| 3–4 | Weak: cluttered, awkward crop, flat light, no clear subject. |
| 1–2 | Fatal flaw the script missed → also set `"reject": true`. |

## What to look at (in this order)

1. **Subject & moment** — Is it obvious what the photo is about within one second?
   Is it the peak moment (expression, gesture, action, interaction), or just before/after?
2. **Light** — Direction and quality (soft side light, golden/blue hour, rim light, dramatic
   contrast) vs. flat midday light, harsh shadows across faces, mixed color casts.
3. **Composition**
   - Rule of thirds / phi grid: subject or horizon on a third line rather than dead centre
     (centre is fine when symmetry is the point).
   - Leading lines, S-curves, diagonals that pull the eye to the subject.
   - Framing within the frame (doorways, arches, foliage).
   - Balance & visual weight; deliberate negative space.
   - Figure-to-ground separation: subject stands out by tone, color, or depth of field.
   - Clean edges: nothing distracting cutting into the corners; no poles "growing" from heads.
   - Layers: foreground / middle / background depth.
4. **Color & tone** — Harmonious palette (complementary, analogous, monochrome), a single
   strong accent color, tonal range with true blacks and controlled highlights.
5. **Edit potential** — Slight underexposure or a crooked horizon is fixable; a missed moment is not.
   Reward frames with good bones.

## Genre checks

**Portrait / people / events**
- Eyes open and sharp, ideally with catchlights; eye contact or a purposeful gaze.
- Natural, flattering expression; no mid-blink, mid-word mouth, or awkward half-smile.
- Don't crop at joints (wrists, elbows, knees, ankles) or the chin/top of head awkwardly.
- Headroom and lead room in the direction the person faces.
- Groups: everyone visible, eyes open, nobody blocked. One person blinking in a group shot → `reject`.
- Background not merging with the head; flattering light on the face.

**Street / travel**
- Decisive moment, juxtaposition, humor, gesture, story.
- Layers and depth; a human element giving scale or narrative.
- Strong light/shadow play and graphic shapes.
- Sense of place: does it say where you were without being a postcard cliché?

**Landscape / architecture**
- Level horizon; converging verticals only when intentional.
- Foreground interest leading into the scene.
- Light quality matters most: golden hour, side light, weather, atmosphere.
- Symmetry and repetition in architecture; clean lines; uncluttered edges.
- Sky: dramatic sky earns space (low horizon); dull sky should be minimized.

## Choosing between near-duplicates

Frames labelled with the same `gN` on the contact sheet are the same scene. Give them
**different** scores — decide which one has the better expression, gesture, timing, or
cleaner composition, and say why in the reason (e.g. "better smile than #14").

## Reasons

One short, concrete phrase in photographer language (≤ 12 words), e.g.
- "Golden side light, subject on left third, clean background"
- "Peak laugh, catchlights, tight crop works"
- "Strong leading lines but cluttered right edge"
- "Flat light, subject centred with no reason" 
