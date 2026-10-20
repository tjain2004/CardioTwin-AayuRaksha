plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

fun prop(name: String, default: String) = (project.findProperty(name) as String?) ?: default

android {
    namespace = "com.cardiotwin.wear"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.cardiotwin.wear"
        minSdk = 30            // Wear OS 3
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
        buildConfigField("String", "DEFAULT_SERVER_URL", "\"${prop("cardiotwin.serverUrl", "http://10.0.2.2:8000")}\"")
        buildConfigField("String", "DEFAULT_PATIENT_ID", "\"${prop("cardiotwin.patientId", "stay-37093652")}\"")
        buildConfigField("String", "DEMO_USER", "\"${prop("cardiotwin.demoUser", "dr.demo")}\"")
        buildConfigField("String", "DEMO_PASSWORD", "\"${prop("cardiotwin.demoPassword", "demo1234")}\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
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
        compose = true
        buildConfig = true
    }
    testOptions {
        unitTests.isReturnDefaultValues = true      // android.util.Log in the links is a no-op in JVM tests
    }
    lint {
        abortOnError = false
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.service)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.foundation)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.wear.compose.material)
    implementation(libs.wear.compose.foundation)
    implementation(libs.wear.input)
    implementation(libs.health.services)
    implementation(libs.play.services.wearable)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.coroutines.play.services)
    implementation(libs.okhttp)
    debugImplementation(libs.compose.ui.tooling)
    debugImplementation(libs.wear.compose.ui.tooling)

    testImplementation(libs.junit)
    testImplementation(libs.json)      // real org.json for JVM unit tests (android.jar only has stubs)
}
