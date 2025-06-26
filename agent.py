# agent.py

import datetime
import json
from typing import Dict, Any

import pytz
from dateparser.search import search_dates
from langgraph.graph import StateGraph

from calendar_utils import GoogleCalendarUtils

import os
import openai

import re
import dateparser
from datetime import datetime, timedelta
from database import (
    save_booking, list_bookings, get_last_booking, cancel_booking, update_booking, get_booking_by_id
)

calendar_utils = GoogleCalendarUtils()


class BookingState(dict):
    pass


def parse_input_node(user_msg: str):
    """
    Calls OpenAI API to extract calendar slot info from user_msg using a strict system prompt.
    """
    SYSTEM_PROMPT = '''
You are a helpful AI assistant that helps users manage their calendar. The user may ask to check availability, book a meeting, or cancel one.

Extract the following information from the user's message and respond with a JSON object:

1. `intent`: One of the following:
   - "book" (if user wants to schedule something)
   - "check" (if user is asking when they're free or available)
   - "cancel" (if they want to remove a booking)
   - "unknown" (if none of the above apply)

2. `datetime`: A date/time string in ISO 8601 format (e.g. "2025-06-28T14:00:00+05:30")  
3. `duration`: Length of the meeting in minutes (default to 30 if not given)  
4. `summary`: What the meeting is about (default: "Meeting")  
5. `timezone`: If the user mentioned a timezone like "Asia/Kolkata", return it; otherwise "UTC"  
6. `ambiguity`: true if the time is vague (e.g. "next week"), or if essential information is missing

Respond in **strict JSON format only**.

Example response:
```json
{
  "intent": "book",
  "datetime": "2025-06-29T15:00:00+05:30",
  "duration": 30,
  "summary": "Team sync",
  "timezone": "Asia/Kolkata",
  "ambiguity": false
}
```
'''
    openai.api_key = os.environ.get("OPENAI_API_KEY")
    if not openai.api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable not set.")
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg}
        ],
        temperature=0.0,
        max_tokens=512
    )
    # Extract JSON from response
    text = response["choices"][0]["message"]["content"]
    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        raise ValueError(f"No JSON found in LLM output: {text}")
    try:
        result = json.loads(match.group(0))
    except Exception as e:
        raise ValueError(f"Invalid JSON from LLM: {text}") from e
    return result


def ask_for_missing_info_node(state: dict):
    slots = state.get("slots", {})
    if not slots.get("date/time"):
        state["response"] = "When would you like to schedule it?"
    elif not slots.get("intent"):
        state["response"] = "Would you like to book a meeting or just check availability?"
    return state


def check_calendar_node(state: dict):
    slots = state.get("slots", {})
    try:
        dt = datetime.datetime.fromisoformat(slots["date/time"])
        duration = slots.get("duration", 30)
        end = dt + datetime.timedelta(minutes=duration)
        busy = calendar_utils.get_free_busy(dt, end)
        state["busy"] = bool(busy)
    except Exception as e:
        state["response"] = f"Couldn't parse date/time: {e}"
    return state


def suggest_alternatives_node(state: dict):
    slots = state.get("slots", {})
    dt = datetime.datetime.fromisoformat(slots["date/time"])
    duration = slots.get("duration", 30)
    end = dt + datetime.timedelta(hours=2)

    alternatives = calendar_utils.find_available_slots(dt, end, duration)
    if alternatives:
        times = [f"{s[0].strftime('%A %I:%M %p')} - {s[1].strftime('%I:%M %p')}" for s in alternatives]
        state["response"] = "You're busy at that time. Available options: " + ", ".join(times)
    else:
        state["response"] = "No alternative free slots found."
    return state


def confirm_booking_node(state: dict):
    slots = state.get("slots", {})
    try:
        dt = datetime.datetime.fromisoformat(slots["date/time"])
        duration = slots.get("duration", 30)
        end = dt + datetime.timedelta(minutes=duration)
        summary = slots.get("summary", "Meeting")

        event = calendar_utils.create_event(dt, end, summary)
        if 'error' in event:
            state["response"] = f"Failed to book: {event['error']}"
        else:
            state["response"] = f"✅ Meeting '{summary}' booked on {dt.strftime('%A, %B %d at %I:%M %p')}"
    except Exception:
        state["response"] = "Booking failed. Please try again."
    return state


def end_conversation_node(state: dict):
    if not state.get("response"):
        state["response"] = "Let me know if you want to schedule another meeting."
    return state


def build_agent():
    graph = StateGraph(BookingState)

    graph.add_node("parse_input", parse_input_node)
    graph.add_node("ask_for_missing_info", ask_for_missing_info_node)
    graph.add_node("check_calendar", check_calendar_node)
    graph.add_node("suggest_alternatives", suggest_alternatives_node)
    graph.add_node("confirm_booking", confirm_booking_node)
    graph.add_node("end_conversation", end_conversation_node)

    graph.add_edge("parse_input", "ask_for_missing_info")
    graph.add_edge("ask_for_missing_info", "check_calendar")
    graph.add_conditional_edges(
        "check_calendar",
        lambda s: "suggest_alternatives" if s.get("busy") else "confirm_booking"
    )
    graph.add_edge("suggest_alternatives", "end_conversation")
    graph.add_edge("confirm_booking", "end_conversation")

    graph.set_entry_point("parse_input")
    graph.set_finish_point("end_conversation")

    return graph.compile(), None


def extract_intent(user_msg):
    msg = user_msg.lower()
    if any(w in msg for w in ["cancel", "delete", "remove"]):
        return "cancel"
    if any(w in msg for w in ["edit", "reschedule", "move", "change"]):
        return "edit"
    if any(w in msg for w in ["book", "schedule", "set up", "add"]):
        return "book"
    if any(w in msg for w in ["list", "show", "what", "upcoming", "events", "history", "held"]):
        return "list"
    if any(w in msg for w in ["free", "available", "slots"]):
        return "check"
    if any(w in msg for w in ["help", "how"]):
        return "help"
    return "unknown"


def extract_attendees(user_msg):
    match = re.search(r"with ([A-Za-z ,and]+)", user_msg, re.I)
    if match:
        names = re.split(r",| and ", match.group(1))
        return [n.strip().title() for n in names if n.strip()]
    return []


def extract_reference(user_msg):
    time_match = re.search(r"(\d{1,2}(:\d{2})?\s*(am|pm)?)", user_msg, re.I)
    summary_match = re.search(r"(event|call|appointment) (about|on|for) ([^,\\.]+)", user_msg, re.I)
    if time_match:
        return time_match.group(0)
    if summary_match:
        return summary_match.group(3).strip()
    if "last" in user_msg.lower():
        return "last"
    if "next" in user_msg.lower():
        return "next"
    if any(w in user_msg.lower() for w in ["it", "that", "this"]):
        return "context"
    return None


def extract_slots(user_msg, context_event=None):
    import logging
    found = search_dates(user_msg, settings={"RETURN_AS_TIMEZONE_AWARE": True, "DATE_ORDER": "DMY"})
    dt = None
    if found:
        dt = found[0][1]
        # If time is missing, try to extract it manually
        if dt.hour == 0 and dt.minute == 0:
            time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)', user_msg, re.I)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2) or 0)
                ampm = time_match.group(3).lower()
                if ampm == 'pm' and hour != 12:
                    hour += 12
                elif ampm == 'am' and hour == 12:
                    hour = 0
                dt = dt.replace(hour=hour, minute=minute)
    duration = 30
    if re.search(r"1 ?hour", user_msg, re.I):
        duration = 60
    elif re.search(r"(\d+)\s*min", user_msg, re.I):
        duration = int(re.search(r"(\d+)\s*min", user_msg, re.I).group(1))
    elif re.search(r"(\d+)\s*hour", user_msg, re.I):
        duration = int(re.search(r"(\d+)\s*hour", user_msg, re.I).group(1)) * 60
    elif context_event and context_event.get("duration"):
        duration = context_event["duration"]
    summary_match = re.search(r"for ([^,\\.;]+)", user_msg, re.I)
    summary = summary_match.group(1).strip() if summary_match else (context_event["summary"] if context_event and context_event.get("summary") else "Event")
    tz_match = re.search(r"([A-Za-z]+/[A-Za-z_]+)", user_msg)
    timezone = tz_match.group(1) if tz_match else (context_event["timezone"] if context_event and context_event.get("timezone") else "UTC")
    attendees = extract_attendees(user_msg) or (context_event["attendees"] if context_event and context_event.get("attendees") else [])
    vague_words = ["next week", "someday", "later", "soon", "whenever", "some time", "not sure"]
    ambiguity = (dt is None) or any(w in user_msg.lower() for w in vague_words)
    reference = extract_reference(user_msg)
    # Logging for debugging
    logging.info(f"Parsed datetime: {dt}, duration: {duration}, timezone: {timezone}, ambiguity: {ambiguity}")
    return {
        "datetime": dt.isoformat() if dt else None,
        "duration": duration,
        "summary": summary,
        "timezone": timezone,
        "attendees": attendees,
        "ambiguity": ambiguity,
        "reference": reference
    }


def find_booking_by_reference(reference, context_event=None):
    bookings = list_bookings()
    if not bookings:
        return None
    if reference == "last":
        return bookings[-1]
    if reference == "next":
        return bookings[0]
    if reference == "context" and context_event:
        # Try to match by context event's summary and time
        for b in bookings:
            if (context_event.get("summary") and context_event["summary"].lower() in b[1].lower()) or \
               (context_event.get("datetime") and context_event["datetime"] in b[3]):
                return b
    for b in bookings:
        if reference and (reference in b[3] or reference.lower() in b[1].lower()):
            return b
    return None


def format_event_natural(b):
    dt = dateparser.parse(b[3])
    if not dt:
        return f"Your event '{b[1]}' (time unknown)."
    dt_local = dt.astimezone(pytz.timezone(b[5])) if b[5] != "UTC" else dt
    date_str = dt_local.strftime("%A, %B %d at %I:%M %p")
    return f"Your event '{b[1]}' is scheduled for {date_str} ({b[5]}). Status: {b[6]}."


def get_context_event_from_history(messages):
    # Find the last assistant message with a booking or event in the response
    for msg in reversed(messages):
        if msg["role"] == "assistant" and "event" in msg["content"].lower():
            # Try to extract event details from the message
            # This is a simple heuristic; you can make it more robust if you store structured data in the session
            match = re.search(r"'(.+?)' (?:booked|scheduled|updated|cancelled).*?for ([\w, :]+)", msg["content"])
            if match:
                summary = match.group(1)
                dt_str = match.group(2)
                dt = dateparser.parse(dt_str)
                return {
                    "summary": summary,
                    "datetime": dt.isoformat() if dt else None,
                    "duration": 30,
                    "timezone": "UTC",
                    "attendees": []
                }
    return None


def handle_user_message(user_msg, messages=None):
    """
    user_msg: str, the current user message
    messages: list of dicts, the chat history (each dict: {"role": "user"/"assistant", "content": str})
    """
    context_event = get_context_event_from_history(messages) if messages else None
    intent = extract_intent(user_msg)
    slots = extract_slots(user_msg, context_event)
    response = ""
    result = {}

    if intent == "book":
        if slots["ambiguity"]:
            response = "Please specify a clear date and time for your event."
        else:
            start_time = slots["datetime"]
            end_time = (dateparser.parse(slots["datetime"]) + timedelta(minutes=slots["duration"])).isoformat()
            for b in list_bookings():
                if b[3] == start_time and b[6] == 'active':
                    response = "You already have an event at that time."
                    break
            else:
                event_id = f"evt_{int(datetime.now().timestamp())}"
                save_booking(slots["summary"], event_id, start_time, end_time, slots["timezone"])
                response = f"Event '{slots['summary']}' booked for {start_time} ({slots['timezone']})."
                if slots["attendees"]:
                    response += f" Attendees: {', '.join(slots['attendees'])}."
    elif intent == "cancel":
        ref = slots.get("reference")
        booking = find_booking_by_reference(ref, context_event) if ref else get_last_booking()
        if booking:
            cancel_booking(booking[0])
            response = f"Cancelled event: '{booking[1]}' at {booking[3]}"
        else:
            response = "No matching event found to cancel."
    elif intent == "edit":
        ref = slots.get("reference")
        booking = find_booking_by_reference(ref, context_event) if ref else get_last_booking()
        if booking:
            if slots["ambiguity"]:
                response = "Please specify the new date/time or summary for your event."
            else:
                start_time = slots["datetime"]
                end_time = (dateparser.parse(slots["datetime"]) + timedelta(minutes=slots["duration"])).isoformat()
                update_booking(booking[0], slots["summary"], start_time, end_time, slots["timezone"])
                response = f"Updated event to '{slots['summary']}' at {start_time} ({slots['timezone']})."
        else:
            response = "No matching event found to edit."
    elif intent == "list":
        bookings = list_bookings()
        held = [b for b in bookings if dateparser.parse(b[3]) < datetime.now(pytz.UTC)]
        upcoming = [b for b in bookings if dateparser.parse(b[3]) >= datetime.now(pytz.UTC)]
        if upcoming or held:
            response = ""
            if upcoming:
                response += "Here are your upcoming events:\n" + "\n".join(
                    [format_event_natural(b) for b in upcoming]
                )
            if held:
                response += "\n\nHere are your past events:\n" + "\n".join(
                    [format_event_natural(b) for b in held]
                )
        else:
            response = "You have no events scheduled."
    elif intent == "check":
        bookings = list_bookings()
        if bookings:
            response = "You are busy at:\n" + "\n".join(
                [f"{b[3]} - {b[4]} ({b[5]})" for b in bookings]
            )
        else:
            response = "You are free! No events scheduled."
    elif intent == "help":
        response = (
            "You can ask me to book, cancel, edit, or list your events. "
            "Examples:\n"
            "- Book an event tomorrow at 10am for 1 hour with Alice\n"
            "- Cancel my last event\n"
            "- Edit my next event to Friday at 3pm\n"
            "- What are my events this week?\n"
            "- You can also use natural language like 'Add a call after lunch next Monday.'"
        )
    else:
        response = "Sorry, I didn't understand. Please try again or type 'help'."

    result.update({"intent": intent, **slots, "response": response})
    return result
