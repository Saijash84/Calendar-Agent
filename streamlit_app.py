# streamlit_app.py

import streamlit as st
import requests

API_URL = "http://localhost:8000/chat"

st.set_page_config(page_title="CalMate", page_icon="📅", layout="wide")
st.title("📅 CalMate: Your Smart Calendar Assistant")
st.caption("Book, edit, cancel, and view your events with natural language.")

st.info(
    "💡 **How to use CalMate:**\n"
    "- Book an event: `Book an event tomorrow at 10am for 1 hour with Alice`\n"
    "- Cancel an event: `Cancel my last event` or `Cancel my 2pm event`\n"
    "- Edit an event: `Reschedule my next event to Friday at 3pm`\n"
    "- List events: `What are my events this week?`\n"
    "- Check availability: `When am I free tomorrow?`\n"
    "- You can also use natural language like 'Add a call after lunch next Monday.'"
)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "events" not in st.session_state:
    st.session_state.events = {"upcoming": [], "held": []}
if "page" not in st.session_state:
    st.session_state.page = "chat"

def fetch_events():
    res = requests.post(API_URL, json={"message": "list"})
    data = res.json()
    upcoming, held = [], []
    if "Here are your upcoming events" in data["response"]["response"]:
        parts = data["response"]["response"].split("\n\n")
        if len(parts) > 1:
            upcoming = [line for line in parts[0].split("\n")[1:] if line.strip()]
            held = [line for line in parts[1].split("\n")[1:] if line.strip()]
        else:
            upcoming = [line for line in parts[0].split("\n")[1:] if line.strip()]
    elif "You have no events scheduled." in data["response"]["response"]:
        upcoming, held = [], []
    st.session_state.events = {"upcoming": upcoming, "held": held}

# Sidebar menu
with st.sidebar:
    st.header("📅 Menu")
    if st.button("Book an Event"):
        st.session_state.page = "chat"
    st.markdown("---")
    st.subheader("Upcoming Events")
    fetch_events()
    if st.session_state.events["upcoming"]:
        for event in st.session_state.events["upcoming"]:
            st.write(event)
    else:
        st.write("No upcoming events.")
    st.subheader("Past Events")
    if st.session_state.events["held"]:
        for event in st.session_state.events["held"]:
            st.write(event)
    else:
        st.write("No past events.")

# Main area
if st.session_state.page == "chat":
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Type your message..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.spinner("Thinking..."):
            try:
                res = requests.post(API_URL, json={"message": prompt})
                res.raise_for_status()
                data = res.json()
                reply = data["response"]["response"]
                st.session_state.messages.append({"role": "assistant", "content": reply})
                with st.chat_message("assistant"):
                    st.markdown(reply)
                fetch_events()
            except Exception as e:
                reply = f"❌ Error: {e}"
                st.session_state.messages.append({"role": "assistant", "content": reply})
                with st.chat_message("assistant"):
                    st.markdown(reply)
