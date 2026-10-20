package com.cardiotwin.wear.data

import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime

/**
 * A medicine on the watch's reminder list. The backend has no medication schedule yet, so the list lives on the watch
 * (a demo default below). Pressing "Taken" sends a dose_taken reading with meta.drug = [generic], which the backend
 * checks against the clinic's daily limit (twin_rules.dose_check). The watch never suggests a dose.
 */
data class Medication(val generic: String, val label: String, val mg: Double, val times: List<LocalTime>)

data class DueDose(val med: Medication, val dueAt: LocalDateTime)

object MedicationSchedule {
    val DEMO = listOf(
        Medication("metoprolol", "Metoprolol 25 mg", 25.0, listOf(LocalTime.of(8, 0), LocalTime.of(20, 0))),
        Medication("aspirin", "Aspirin 75 mg", 75.0, listOf(LocalTime.of(9, 0))),
    )

    /** The next dose at or after [now] across all medicines (looks up to two days ahead). */
    fun next(meds: List<Medication>, now: LocalDateTime): DueDose? =
        meds.flatMap { m ->
            (0L..1L).flatMap { d -> m.times.map { t -> DueDose(m, LocalDateTime.of(LocalDate.from(now).plusDays(d), t)) } }
        }.filter { !it.dueAt.isBefore(now) }.minByOrNull { it.dueAt }
}
