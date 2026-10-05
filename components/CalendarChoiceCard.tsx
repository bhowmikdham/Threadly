import { useEffect, useState } from "react"

import type { CalendarChoices } from "../lib/types"

import "./CalendarChoiceCard.css"

export function CalendarChoiceCard({
  value,
  enabled,
  choose
}: {
  value: CalendarChoices
  enabled: boolean
  choose: (choiceId: string) => void
}) {
  const deadline = Date.parse(value.expires_at)
  const [expired, setExpired] = useState(!(deadline > Date.now()))
  useEffect(() => {
    const remaining = deadline - Date.now()
    setExpired(!(remaining > 0))
    if (!(remaining > 0)) return
    const timer = setTimeout(
      () => setExpired(true),
      Math.min(remaining, 2_147_483_647)
    )
    return () => clearTimeout(timer)
  }, [deadline])
  const choices = value.choices.filter((choice) => choice.access === "editable")
  return (
    <section
      className="calendar-choice-card"
      aria-label="Choose an event calendar">
      <p>Choose a calendar for this event</p>
      <div className="calendar-choice-buttons">
        {choices.map((choice) => (
          <button
            key={choice.choice_id}
            disabled={!enabled || expired}
            onClick={() => choose(choice.choice_id)}>
            {choice.label}
          </button>
        ))}
      </div>
      <small>
        {expired
          ? "These choices expired. Ask to choose the calendar again."
          : !choices.length
            ? "No editable calendars are available. Review Calendar settings."
            : "Your event details are kept. Your current Calendar approval setting still applies."}
      </small>
    </section>
  )
}
