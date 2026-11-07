// SPDX-License-Identifier: GPL-3.0-only
// scRGB/Rec.709 linear residual controls. No SDR clamp or tone mapping.
// PQ/HLG adapters decode transfer and use Rec.2020 weights before this formula.
float3 ApplyHdrResidualControls(float3 base, float3 residual, float multiplier,
    float shadows, float highlights, float saturation, float lightness) {
    float3 weights = float3(0.2126, 0.7152, 0.0722);
    residual *= multiplier;
    float dy = dot(residual, weights);
    residual *= dy < 0.0 ? shadows : (dy > 0.0 ? highlights : 1.0);
    float luma = dot(residual, weights);
    return base + luma * lightness + (residual - luma) * saturation;
}
