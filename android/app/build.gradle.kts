import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val keystorePropsFile = rootProject.file("keystore.properties")
val keystoreProps = Properties()
if (keystorePropsFile.isFile) {
    keystorePropsFile.inputStream().use { keystoreProps.load(it) }
}

fun signingSecret(envName: String, propName: String): String {
    val fromEnv = System.getenv(envName)?.trim().orEmpty()
    if (fromEnv.isNotEmpty()) return fromEnv
    return keystoreProps.getProperty(propName)?.trim().orEmpty()
}

android {
    namespace = "com.wadeboyes.grokremote"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.wadeboyes.grokremote"
        // Galaxy S24 Ultra and Galaxy Tab S10 both shipped on Android 14.
        minSdk = 34
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
        vectorDrawables.useSupportLibrary = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
    }
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    signingConfigs {
        create("sideload") {
            storeFile = rootProject.file("keystore/grok-remote-release.jks")
            storePassword = signingSecret("GROK_REMOTE_STORE_PASSWORD", "storePassword")
            keyAlias = signingSecret("GROK_REMOTE_KEY_ALIAS", "keyAlias").ifEmpty { "grokremote" }
            keyPassword = signingSecret("GROK_REMOTE_KEY_PASSWORD", "keyPassword")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("sideload")
        }
        debug {
            signingConfig = signingConfigs.getByName("sideload")
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.10.01")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    debugImplementation("androidx.compose.ui:ui-tooling")
}

val validateSideloadKeystore = tasks.register("validateSideloadKeystore") {
    doLast {
        val cfg = android.signingConfigs.getByName("sideload")
        val file = cfg.storeFile
        if (file == null || !file.isFile) {
            throw GradleException(
                "Missing android/keystore/grok-remote-release.jks. See android/README.md."
            )
        }
        if (cfg.storePassword.isNullOrBlank() || cfg.keyPassword.isNullOrBlank()) {
            throw GradleException(
                "Missing keystore password. Set GROK_REMOTE_STORE_PASSWORD and " +
                    "GROK_REMOTE_KEY_PASSWORD, or create android/keystore.properties " +
                    "from keystore.properties.example. Do not commit the password."
            )
        }
    }
}

tasks.matching { it.name == "assembleRelease" || it.name == "assembleDebug" }.configureEach {
    dependsOn(validateSideloadKeystore)
}
