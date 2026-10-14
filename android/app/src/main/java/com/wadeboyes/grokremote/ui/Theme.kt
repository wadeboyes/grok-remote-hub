package com.wadeboyes.grokremote.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

val Bg = Color(0xFF0A0C10)
val Surface = Color(0xFF121722)
val Surface2 = Color(0xFF181F2A)
val Border = Color(0xFF2A3548)
val TextMain = Color(0xFFE8EDF5)
val Muted = Color(0xFF8B97A8)
val Accent = Color(0xFFE6B84D)
val AccentText = Color(0xFF1A1200)
val Danger = Color(0xFFE06A6A)

/** At least 44px on mdpi, and larger on the S24 Ultra and Tab S10. */
val MinTouch = 48.dp

/** Wide window (tablet landscape, and tablet portrait when it is wide). */
val TwoPaneMin = 600.dp

private val Colors = darkColorScheme(
    primary = Accent,
    onPrimary = AccentText,
    background = Bg,
    onBackground = TextMain,
    surface = Surface,
    onSurface = TextMain,
    surfaceVariant = Surface2,
    onSurfaceVariant = Muted,
    outline = Border,
    error = Danger,
)

private val Type = Typography(
    headlineSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 22.sp,
        lineHeight = 28.sp,
        color = TextMain,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 18.sp,
        lineHeight = 24.sp,
        color = TextMain,
    ),
    bodyLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        lineHeight = 22.sp,
        color = TextMain,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 15.sp,
        lineHeight = 21.sp,
        color = TextMain,
    ),
    labelLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 15.sp,
        lineHeight = 20.sp,
    ),
    bodySmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 13.sp,
        lineHeight = 18.sp,
        color = Muted,
    ),
)

@Composable
fun GrokTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = Colors, typography = Type, content = content)
}
