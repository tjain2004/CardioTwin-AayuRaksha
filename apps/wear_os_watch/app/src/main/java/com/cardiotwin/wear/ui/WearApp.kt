package com.cardiotwin.wear.ui

import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.foundation.layout.fillMaxSize
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.wear.compose.material.HorizontalPageIndicator
import androidx.wear.compose.material.PageIndicatorState
import androidx.wear.compose.material.Scaffold
import androidx.wear.compose.material.TimeText
import com.cardiotwin.wear.UiJump
import com.cardiotwin.wear.WatchRepository
import com.cardiotwin.wear.ui.screens.AlertScreen
import com.cardiotwin.wear.ui.screens.EmergencyScreen
import com.cardiotwin.wear.ui.screens.FallScreen
import com.cardiotwin.wear.ui.screens.MedicationScreen
import com.cardiotwin.wear.ui.screens.OverviewScreen
import com.cardiotwin.wear.ui.screens.SetupScreen
import com.cardiotwin.wear.ui.screens.StatusScreen
import com.cardiotwin.wear.ui.theme.CardioTwinTheme
import kotlinx.coroutines.launch

private const val PAGE_OVERVIEW = 0
private const val PAGE_STATUS = 1
private const val PAGE_ALERTS = 2
private const val PAGE_MEDS = 3
private const val PAGE_SETUP = 4
private const val PAGES = 5

/**
 * Swipe left/right between Overview, Status, Alerts, Medication and Setup. The fall check, the emergency and a due
 * medication reminder take over the screen when they happen.
 */
@Composable
fun WearApp(repo: WatchRepository, onOpenPhone: (String?) -> Unit, onModeChanged: () -> Unit) {
    val state by repo.state.collectAsStateWithLifecycle()
    val pager = rememberPagerState { PAGES }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        repo.jumps.collect { j -> pager.animateScrollToPage(if (j == UiJump.ALERTS) PAGE_ALERTS else PAGE_MEDS) }
    }

    CardioTwinTheme {
        val emergency = state.emergency
        val fall = state.fallCheck
        when {
            emergency != null && !state.emergencyHidden ->
                EmergencyScreen(emergency, state.simulated || state.vitals.values.any { it.simulated },
                    onAction = { onOpenPhone(emergency.actionLabel) }, onHide = repo::hideEmergency)
            fall != null -> FallScreen(fall, onOk = { repo.answerFall("im_ok") }, onHelp = { repo.answerFall("need_help") })
            else -> {
                val indicator = remember(pager) {
                    object : PageIndicatorState {
                        override val pageOffset: Float get() = pager.currentPageOffsetFraction
                        override val selectedPage: Int get() = pager.currentPage
                        override val pageCount: Int get() = PAGES
                    }
                }
                Scaffold(
                    timeText = { if (pager.currentPage != PAGE_STATUS) TimeText() },
                    pageIndicator = { HorizontalPageIndicator(pageIndicatorState = indicator) },
                ) {
                    HorizontalPager(state = pager, modifier = Modifier.fillMaxSize()) { page ->
                        when (page) {
                            PAGE_OVERVIEW -> OverviewScreen(state,
                                onOpenStatus = { scope.launch { pager.animateScrollToPage(PAGE_STATUS) } },
                                onOpenSetup = { scope.launch { pager.animateScrollToPage(PAGE_SETUP) } })
                            PAGE_STATUS -> StatusScreen(state)
                            PAGE_ALERTS -> AlertScreen(state, onSeen = repo::dismissAlert)
                            PAGE_MEDS -> MedicationScreen(state, onTaken = repo::doseTaken, onSnooze = { repo.doseSnooze() },
                                onSkip = repo::doseSkip, onDemo = repo::demoReminderInOneMinute)
                            else -> SetupScreen(repo, state, onModeChanged)
                        }
                    }
                }
            }
        }
    }
}
