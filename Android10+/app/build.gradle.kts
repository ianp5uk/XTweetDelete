plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.waysproperty.tweetdelete"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.waysproperty.tweetdelete"
        // Android 10 (2019) floor: covers current GrapheneOS/LineageOS builds and
        // the overwhelming majority of active devices, without carrying legacy
        // compatibility shims for pre-scoped-storage Android.
        minSdk = 29
        targetSdk = 34
        versionCode = 3
        versionName = "1.0.2"
    }

    signingConfigs {
        create("release") {
            storeFile = file("../keystore/tweetdelete-release.keystore")
            storePassword = System.getenv("TD_KEYSTORE_PASSWORD") ?: "tweetdelete"
            keyAlias = "tweetdelete"
            keyPassword = System.getenv("TD_KEY_PASSWORD") ?: "tweetdelete"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Self-signed release build for direct/sideload distribution (no Play
            // Store). Replace with your own keystore before real distribution —
            // see the project README for how the placeholder key here was made.
            signingConfig = signingConfigs.getByName("release")
        }
        debug {
            applicationIdSuffix = ".debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        viewBinding = false
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.9.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("androidx.webkit:webkit:1.11.0")
    // Tiny embedded HTTP server, standing in for server.py's job on Android:
    // serves the bundled web UI and reverse-proxies /api/x/* to api.x.com so
    // the WebView's fetch() calls are same-origin (CORS never applies to a
    // native client making the actual upstream request, only to the browser
    // leg, which now only ever talks to 127.0.0.1).
    implementation("org.nanohttpd:nanohttpd:2.3.1")
    // Used inside the proxy to forward requests/responses to api.x.com.
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}
