# Neu.Tail demo product images

These eight WebP assets were AI-generated for the products returned by the
Olivia demo customer's homepage recommendation flow. They are intentionally
brand-free catalogue images with a consistent warm-ivory studio treatment.

| SKU | Product |
| --- | --- |
| `SKU00008` | Maison Verve Blue Coat |
| `SKU00064` | Bellamy & Co Pink Coat |
| `SKU00081` | Everyday Edit Black Blazer |
| `SKU00094` | Maison Verve Grey Day Dress |
| `SKU00136` | NeuBasics Burgundy Occasion Dress |
| `SKU00158` | LuxeForm White Maxi Dress |
| `SKU00165` | Aurelia Grey Occasion Dress |
| `SKU00185` | Elm & Stone Burgundy Blazer |

The delivery files are 900 x 1200 WebP images. Identical copies belong in the
frontend's `public/products` directory so the relative URLs returned by the API
(`/products/<SKU>.webp`) resolve in both Vite development and production
builds.

Generation prompt pattern:

> Photorealistic ecommerce product-mockup of the database-defined garment on
> an invisible mannequin, centered and fully visible on a seamless warm-ivory
> studio backdrop, soft diffused lighting and a subtle grounding shadow; one
> garment only, with no person, hanger, props, text, brand label, logo, or
> watermark.
