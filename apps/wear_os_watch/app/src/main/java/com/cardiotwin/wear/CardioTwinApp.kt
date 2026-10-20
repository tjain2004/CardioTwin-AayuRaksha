package com.cardiotwin.wear

import android.app.Application

class CardioTwinApp : Application() {
    val repository: WatchRepository by lazy { WatchRepository(this) }
}
