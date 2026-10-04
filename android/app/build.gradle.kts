plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "app.hole.android"
    compileSdk = 36

    defaultConfig {
        applicationId = "app.hole.android"
        minSdk = 26
        // 34 rather than 35 or later: from 35 the system draws the status and navigation bars
        // over the app, and every screen would have to inset itself. Raise it with that work.
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    sourceSets {
        // The unit tests read the same services.json and extractors.js the app ships.
        getByName("test").resources.srcDir("src/main/assets")
    }

    lint {
        // OldTargetApi: targetSdk 34 is a choice, explained above. SetTextI18n: the app is in
        // English only. The two version checks need the network and only say "newer exists".
        // ObsoleteSdkInt: the adaptive icon has to stay in mipmap-anydpi-v26, because aapt2 does not
        // resolve the manifest's @mipmap/ic_launcher to a bare mipmap-anydpi folder (tried).
        disable += setOf("OldTargetApi", "SetTextI18n", "NewerVersionAvailable", "AndroidGradlePluginVersion", "ObsoleteSdkInt")
    }

    testOptions {
        unitTests.all {
            // The default starts the test JVM with a heap a sixteenth of the machine's memory,
            // which is a failed start on one that is short of it. These tests need very little.
            it.minHeapSize = "16m"
            it.maxHeapSize = "256m"
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    // The JVM has no org.json (Android supplies it), and the tests parse the server's JSON.
    testImplementation("org.json:json:20240303")
}
