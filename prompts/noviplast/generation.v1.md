<!-- prompt_version: v1 | client: noviplast -->
# Noviplast product-copy voice & contract (v1)

The frozen brand-voice template for generating Noviplast product copy. It is loaded by the
producer — Claude in a Cowork session (the `content-generator` skill) now, and the headless API
backend later — for one `(gtin, language)` at a time. The few-shot examples below **are** the `v1`
voice: changing them is a new voice and requires bumping `prompt_version` (which re-fingerprints and
invalidates the cache). Keep this file and `lib.generator.DEFAULT_PROMPT_VERSION` in step.

Noviplast sells practical Dutch/French garden and household products. Only `nl` and `fr` exist.

## What to produce

For each request, return one ranked `usps` list (and, only when asked, a `product_name`):

- **`usps[0]` = the tagline.** One short benefit line — the page headline, the header-video caption,
  and the opening line of the description. **Not** the raw marketing message (attr 1083); write a
  crisp line *from* it. Aim ~30–60 characters. The real live page tagline is
  `Reinigingssticks voor je afvoer` (31 chars).
- **`usps[1:]` = the Eigenschappen bullets.** The generated benefit list, most important first. Each
  ideally ≤ 80 characters (one readable line). Two to four is typical; one strong tagline alone is
  valid when there is nothing more to say.
- **`product_name`** — supply **only** when the request's `needs_name` is true (the feed carries no
  name in this language, so translate the Dutch/functional name). Otherwise omit it.

Do **not** produce the Technische-details block (net content, dimensions, material) — those are
assembled deterministically downstream. Never put a measurement, dimension, or material into `usps`.

## The two modes

- **`mode = "tighten"`** — the feed already carries usable copy (attr 1067) in `candidates`, but it
  is too long. **Shorten and rank those candidates** into the tagline + bullets; keep their meaning,
  do not invent new claims.
- **`mode = "generate"`** — no usable feed USP. Write from `inputs.marketing_message` (attr 1083),
  using `functional_name`, `net_content`, dimensions, and `material` as context. If
  `marketing_message` is blank, write from `functional_name` and whatever context exists — keep it
  honest and minimal; the blank input is separately flagged for the operator.

## Voice, per language

Benefit-first, second person, everyday-practical. Say what the product does *for the user*.

**Dutch (`nl`)** — terse fragments, verb-less or a single imperative, **no trailing period**,
~30–55 chars. Address the user with `je` / `uw`. Verified live examples:

- `Verwijder makkelijk beschadigde schroeven`
- `Snel en makkelijk onkruid verwijderen`
- `Het perfecte gereedschap voor alle elastische voegen`
- `Voor binnen en buiten, voor droge en natte ondergrond`
- `Sterk speelgoed voor uw huisdier`
- `Uitschuifbaar anti-statisch en buigbaar`
- `Super absorberende spons`
- `Stevige kruk, opvouwbaar`

**French (`fr`)** — imperative-led (`Protégez…`, `Nettoyez…`, `Coupez…`) or a `Un/Une + adjectief`
noun phrase, ~50–90 chars, usually ending with a period. Address the user with `vous` / `vos`.
Verified live examples:

- `Protégez vos repas des insectes avec cet accessoire ingénieux et efficace.`
- `Dites adieu aux vis endommagées grâce à cet outil pratique et efficace.`
- `Le gant parfait pour un nettoyage impeccable et sans effort.`
- `Une lampe élégante et sans piles, parfaite pour la maison ou le camping.`
- `Offrez à vos jambes le confort ultime avec ce coussin ergonomique.`
- `Pelle à poussière extensible extrêmement maniable`

Taglines run tighter than the full examples above; a tagline is the *shortest* honest benefit line,
the bullets carry the detail.

## Worked example (the live page shape)

For the "Drain sticks" product, in Dutch, the page reads:

- tagline: `Reinigingssticks voor je afvoer`
- Eigenschappen: `12 sticks voor het hele jaar`, `Voorkomt extra onderhoud`

So the result would be:

```json
{ "usps": ["Reinigingssticks voor je afvoer", "12 sticks voor het hele jaar", "Voorkomt extra onderhoud"] }
```

## Do not

- **Do not** paste the raw 1083 marketing message as the tagline. Much of the feed's French 1083 is a
  150–1400 character paragraph — e.g. `Découvrez l'outil parfait pour tous vos besoins en joints
  élastiques ! Que vous soyez un professionnel du bâtiment…`. That is tone reference, never a tagline.
- **Do not** surface junk material or placeholder values (e.g. `zzzanders`, any `zzz…` value) — treat
  them as absent.
- **Do not** name a sub-brand (`Novi Twister`, `Hydro Jet`, `Insta Heater`, …) unless the source copy
  for this product already names it.
- **Do not** emit specs (size, weight, material, contents count) as USPs — those are deterministic and
  added elsewhere. A count that is genuinely a selling point (`12 sticks voor het hele jaar`) is fine
  as a benefit bullet; a bare dimension is not.
