# Writing prompts that draw well

## Shape of a row's `prompt`

One component per bullet, in this order (leave out a bullet that does not apply). People scan it in the
dashboard, and Gemini follows it as well as prose:

```
- Place: underground subway platform with tiled walls and warm lighting
- Situation: a visitor is lost and asks which way to go
- People: a visitor in his thirties; a friendly local commuter in her sixties
- Action: he holds up a phone showing a map and asks; she points toward the right exit
- Camera: medium shot at eye level, both faces visible
- Details: a train arriving in soft motion blur in the background
- Avoid: readable text, transit logos
```

For a still life or product, use `Subject` instead of `People` / `Action`. The tool appends the batch style as a
last `- Style:` bullet. Light, colour palette, medium and "no watermark" belong in the **batch style**, not in
each row.

## Batch style examples

| Look | Style text |
|---|---|
| Editorial photo | Photorealistic editorial photo, natural window light, warm neutral palette, shallow depth of field, no text, no logos, no watermark. |
| Flat illustration | Flat vector illustration, limited palette of 4 colours (#1F5F8B, #F2B84B, #F5F4F0, #1C1C1A), simple shapes, no gradients, no outlines, no text. |
| Product on white | Studio product photo on a seamless pure white background, soft even light, subtle shadow under the object, object centred and filling about 80% of the frame, no props, no text. |
| Isometric 3D | Soft 3D isometric render, clay-like materials, pastel colours, gentle ambient occlusion, plain light background, no text. |

## Good / bad

| Bad | Why | Good |
|---|---|---|
| A healthy office | No subject, nothing to draw | A woman in her forties at a standing desk, laptop open, a glass of water and an apple beside the keyboard, bright open-plan office in the background, medium shot from the side |
| Beautiful, amazing, 8k, best quality | Filler words change nothing | (drop them; describe light and framing instead) |
| Salad, photorealistic, warm light, no text | Style repeated per row | Overhead view of a colourful grain salad in a white bowl on a light wooden desk next to a keyboard |
| Team meeting with the Google logo on the screen | Real brand | Team meeting, a blank presentation screen in the background |

## Variation matrix example (topic: office wellness)

| Theme | Setting | Shots |
|---|---|---|
| Movement | desk, stairs, walking meeting | person stretching (medium), feet on stairs (close), two colleagues walking (wide) |
| Food | desk lunch, office kitchen | overhead lunch box, person cutting fruit (hands only) |
| Habits | desk | water bottle with time marks (still life), phone reminder blurred (close) |

Each cell becomes one row; each row reads as a different picture in the dashboard.

## Aspect ratio per use

| Use | Ratio |
|---|---|
| Website hero / YouTube thumbnail | 16:9 |
| Blog header | 3:2 or 16:9 |
| Instagram feed | 4:5 or 1:1 |
| Story / Reels | 9:16 |
| Icons, avatars, product grid | 1:1 |
