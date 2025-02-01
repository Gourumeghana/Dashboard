import hashlib
import pymongo
import streamlit as st
import re
import requests
import folium
from geopy.geocoders import Nominatim
from langchain import PromptTemplate
from langchain.chains import LLMChain
from langchain.llms.huggingface_hub import HuggingFaceHub
from streamlit_folium import folium_static

# MongoDB setup
client = pymongo.MongoClient("mongodb://localhost:27017/")
db = client["health_app"]  # Database name
users_collection = db["users"]  # Collection name

# Password hashing
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# Function to validate email
def is_valid_email(email):
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email)

# Function to validate password
def is_valid_password(password):
    if len(password) < 8:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[0-9]", password):
        return False
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        return False
    return True

# Function for signup
def signup_user(username, email, password):
    if users_collection.find_one({"username": username}):
        return False, "Username already exists. Please choose a different username."
    if users_collection.find_one({"email": email}):
        return False, "Email already exists. Please log in."
    hashed_password = hash_password(password)
    users_collection.insert_one({
        "username": username,
        "email": email,
        "password": hashed_password
    })
    return True, "Signup successful! Please log in."

# Function for login
def login_user(email, password):
    user = users_collection.find_one({"email": email})
    if user and user["password"] == hash_password(password):
        return True, user["username"]
    return False, "Invalid email or password."

# Step 1: Define a prompt template
prompt_template = PromptTemplate(
    input_variables=["symptoms"],
    template="""
    You are a highly knowledgeable medical assistant. Based on the symptoms provided, diagnose the most probable disease and suggest clear, actionable precautions. Also, provide suitable workout and diet suggestions. Format your response as follows:

    - Disease: [Disease Name]
    - Precautions: [Precautions]
    - Workouts: [Workouts]
    - Diet: [Diet Suggestions]

    Example 1:
    Symptoms: fever, cough, headache
    Response:
    - Disease: Common Cold
    - Precautions: Rest, drink plenty of fluids, and take over-the-counter medications.
    - Workouts: Light stretching, yoga, or rest if unwell.
    - Diet: Warm soups, herbal teas, and vitamin C-rich fruits like oranges.

    Example 2:
    Symptoms: chest pain, shortness of breath
    Response:
    - Disease: Heart Attack
    - Precautions: Seek immediate medical attention, avoid physical exertion, and stay calm.
    - Workouts: None until cleared by a doctor.
    - Diet: Low-sodium, heart-healthy foods like leafy greens, nuts, and whole grains.

    Now analyze the following symptoms:
    Symptoms: {symptoms}

    Response:
    """
)

# Step 2: Configure the LLM
llm = HuggingFaceHub(
    repo_id="google/flan-t5-large",
    model_kwargs={"temperature": 0.7, "max_length": 256},
    huggingfacehub_api_token="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"
)

llm_chain = LLMChain(prompt=prompt_template, llm=llm)

# Step 3: Define a function to predict disease, precautions, workouts, and diet
def predict_health_advice(symptoms):
    symptoms_input = ", ".join(symptoms)
    try:
        response = llm_chain.run(symptoms=symptoms_input)
        if not response.strip():
            return "- Disease: Unknown\n- Precautions: Consult a doctor for more information.\n- Workouts: Not applicable.\n- Diet: Not applicable."

        lines = response.split("\n")
        disease_line = next((line for line in lines if line.lower().startswith("- disease:")), None)
        precautions_line = next((line for line in lines if line.lower().startswith("- precautions:")), None)
        workouts_line = next((line for line in lines if line.lower().startswith("- workouts:")), None)
        diet_line = next((line for line in lines if line.lower().startswith("- diet:")), None)

        if not all([disease_line, precautions_line, workouts_line, diet_line]):
            return f"\n{response}"

        return f"{disease_line}\n{precautions_line}\n{workouts_line}\n{diet_line}"

    except Exception as e:
        return f"Error predicting health advice: {str(e)}"

# Function to geocode a place name to latitude and longitude
def geocode_place(place_name):
    geolocator = Nominatim(user_agent="hospital-finder")
    location = geolocator.geocode(place_name)
    if location:
        return location.latitude, location.longitude
    return None

# Function to fetch nearby hospitals using the Overpass API
# Function to fetch nearby hospitals using the Overpass API
def fetch_hospitals(lat, lon, radius=10000, limit=10):
    overpass_url = f"""
    [out:json];
    (
        node["amenity"="hospital"](around:{radius},{lat},{lon});
        way["amenity"="hospital"](around:{radius},{lat},{lon});
        relation["amenity"="hospital"](around:{radius},{lat},{lon});
    );
    out center;  // Use 'out center' to ensure we get center points for ways and relations
    """
    response = requests.get(f"https://overpass-api.de/api/interpreter?data={overpass_url}")
    data = response.json()
    hospital_markers = []

    if "elements" in data:
        for hospital in data["elements"]:
            # Extracting latitude and longitude
            hospital_lat = hospital.get("lat", hospital.get("center", {}).get("lat"))
            hospital_lon = hospital.get("lon", hospital.get("center", {}).get("lon"))
            if hospital_lat and hospital_lon:
                hospital_markers.append({
                    "lat": hospital_lat,
                    "lon": hospital_lon,
                    "name": hospital.get("tags", {}).get("name", "Unnamed Hospital")
                })
    return hospital_markers[:limit]

# Function to display map with hospitals and routes
# Function to display map with hospitals and routes
def display_map_with_routes(lat, lon, hospitals):
    map_center = [lat, lon]
    hospital_map = folium.Map(location=map_center, zoom_start=13)

    # Mark the user's searched location
    folium.Marker(
        [lat, lon],
        popup="Your Location",
        icon=folium.Icon(color="blue", icon="home"),
    ).add_to(hospital_map)

    # Add hospital markers and generate accurate directions
    for hospital in hospitals:
        hospital_lat = hospital["lat"]
        hospital_lon = hospital["lon"]

        # Construct the correct Google Maps URL
        route_url = f"https://www.google.com/maps/dir/?api=1&origin={lat},{lon}&destination={hospital_lat},{hospital_lon}&travelmode=driving"
        popup_content = f"""
        <b>{hospital['name']}</b><br>
        <a href="{route_url}" target="_blank">Get Directions</a>
        """
        folium.Marker(
            [hospital_lat, hospital_lon],
            popup=folium.Popup(popup_content, max_width=300),
            icon=folium.Icon(color="red", icon="plus"),
        ).add_to(hospital_map)

    return hospital_map




# Streamlit Navbar
st.markdown("""
    <style>
    .navbar {
        background-color: #00796B;
        padding: 10px;
        color: white;
        display: flex;
        align-items: center;
        justify-content: space-between;
        border-radius: 5px;
    }
    .navbar img {
        height: 40px;
        margin-right: 10px;
    }
    .header-title {
        font-size: 1.8em;
        font-weight: bold;
    }
    </style>
    <div class="navbar">
        <div style="display: flex; align-items: center;">
            <img src="https://img.icons8.com/color/48/000000/hospital-room.png" alt="logo" />
            <div class="header-title">Health Recommendation System</div>
        </div>
    </div>
""", unsafe_allow_html=True)

# Initialize session state
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.username = None
    st.session_state.hide_login = False  # Initialize the hide_login state

# Authentication Options
if not st.session_state.authenticated:
    st.sidebar.title("Authentication")
    auth_option = st.sidebar.radio("Choose an option:", ("Signup", "Login"))

    if auth_option == "Signup":
        st.subheader("Signup")
        username = st.text_input("Username")
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        confirm_password = st.text_input("Confirm Password", type="password")

        if st.button("Signup"):
            if not username:
                st.error("Username is required!")
            elif not email:
                st.error("Email is required!")
            elif not is_valid_email(email):
                st.error("Invalid email format!")
            elif not is_valid_password(password):
                st.error("Password must be at least 8 characters, include an uppercase letter, a number, and a special character.")
            elif password != confirm_password:
                st.error("Passwords do not match!")
            else:
                success, message = signup_user(username, email, password)
                if success:
                    st.success(message)
                else:
                    st.error(message)

    if auth_option == "Login":
        st.subheader("Login")
        if not st.session_state.hide_login:
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")

            if st.button("Login"):
                if not email:
                    st.error("Email is required!")
                elif not password:
                    st.error("Password is required!")
                else:
                    success, username = login_user(email, password)
                    if success:
                        st.session_state.authenticated = True
                        st.session_state.username = username
                        st.session_state.hide_login = True  # Hide login form after successful login
                        st.success(f"Welcome back, {username}! click on login button again")

                    else:
                        st.error("Invalid email or password.")

# Ensure the login form is hidden after successful login
if st.session_state.authenticated:
    st.sidebar.title("Menu")
    menu_option = st.sidebar.radio("Select an option:", ("Home", "About", "Developer", "Contact"))

    if menu_option == "About":
        st.markdown("""
             <style>
                .about-section {
                background-color: #f9f9f9;
                border-radius: 10px;
                padding: 20px;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                font-family: 'Arial', sans-serif;
                }
                .about-section h3 {
                color: #00796B;
                font-size: 1.8em;
                margin-bottom: 10px;
                }
                .about-section h5 {
                color: #00796B;
                font-size: 1.3em;
                margin-top: 15px;
                }
                .about-section p {
                color: #333;
                font-size: 1em;
                line-height: 1.6;
                }
            </style>
            <div class="about-section">
                <h3>About Us</h3>
                <p>Welcome to Medical Health center, where health meets technology for a brighter, healthier future.</p>
                <h5>Our Vision</h5>
                <p>We envision a world where access to healthcare information is not just a luxury but a fundamental right. Our journey began with a simple yet powerful idea: to empower individuals with the knowledge and tools they need to take control of their health.</p>
                <h5>Who We Are</h5>
                <p>We are a passionate team of technology enthusiasts who share a common goal: to make healthcare accessible, understandable, and personalized for you. We've come together to create this platform as a testament to our commitment to your well-being.</p>
                <h5>Our Mission</h5>
                <p>At this website, our mission is to provide you with a seamless and intuitive platform that leverages the power of artificial intelligence and machine learning. We want to assist you in identifying potential health concerns based on your reported symptoms, all while offering a wealth of educational resources to enhance your health literacy.</p>
                <h5>How We Do It</h5>
                <p>Our platform utilizes a robust machine learning model trained on a vast dataset of symptoms and diseases. By inputting your symptoms, our system generates accurate predictions about potential illnesses, allowing you to make informed decisions about your health.</p>
                <h5>Your Well-being, Our Priority</h5>
                <p>Your health is our top priority. We understand that navigating the complexities of healthcare can be daunting. That's why we've gone the extra mile to provide not only accurate predictions but also comprehensive information about each disease. You'll find descriptions, recommended precautions, medications, dietary advice, and workout tips to support your journey to better health.</p>
            </div>
        """,unsafe_allow_html=True)
    elif menu_option == "Developer":
        st.markdown("""
             <style>
                .developer-section {
                background-color: #f9f9f9;
                border-radius: 10px;
                padding: 20px;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                font-family: 'Arial', sans-serif;
                }
                .developer-section h3 {
                color: #00796B;
                font-size: 1.8em;
                margin-bottom: 10px;
                }
                .developer-section h5 {
                color: #00796B;
                font-size: 1.3em;
                margin-top: 15px;
                }
                .developer-section p {
                color: #333;
                font-size: 1em;
                line-height: 1.6;
                }
            </style>
            <div class="developer-section">
                <h3>Meet the Developers</h3>
                <p>We are passionate about leveraging the power of artificial intelligence and machine learning to create innovative solutions that can positively impact lives.</p>
                <h5>Developer Information</h5>
                <p>Meghana Gouru<br>Aishwarya Koyyada<br>Sindhuja Alle</p>
                <h5>Our Commitment to Excellence</h5>
                <p>Driven by excellence, we aim to create user-friendly, cutting-edge solutions that improve lives. Whether it's developing predictive models, intelligent algorithms, or designing seamless interfaces, we always strive for innovation and accessibility.</p>
            </div>
        """, unsafe_allow_html=True)


    elif menu_option == "Contact":
        st.markdown("""
             <style>
                .contact-section {
                background-color: #f9f9f9; 
                border-radius: 10px;
                padding: 20px;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); /* Subtle shadow for depth */
                margin: 20px 0;
                font-family: Arial, sans-serif;
                }
                .contact-section h3 {
                color: #00796B;
                font-size: 1.8em;
                margin-bottom: 10px;
                }
                .contact-section h5 {
                color: #00796B;
                font-size: 1.3em;
                margin-top: 15px;
                }
                .contact-section p {
                color: #333;
                font-size: 1em;
                line-height: 1.6;
                }
                .contact-section h6 {
                color: #000000;
                margin: 5px 0;
                font-size: 15px;
                }
            </style>
            <div class="contact-section">
                 <h3>Contact Us</h3>
                 <p>Have questions or need assistance? We're here to help!</p>
                 <h5>Get in Touch</h5>
                 <p>Feel free to reach out to us via email below. We value your feedback and are committed to providing you with the best possible experience.</p>
                 <h6>gourumeghana@gmail.com<br>allesindhuja@gmail.com<br>aishukoyyada@gmail.com</h6>
                 <h5>Location</h5>
                 <p>RGUKT, Basar</p>
            </div>
        """,unsafe_allow_html=True)

    # Feature: Predict Health Advice
    if menu_option == "Home":
        st.markdown("""
            <h3 style="color: #00796B;">
             Welcome to the Health Recommendation System!
            </h3>
        """, unsafe_allow_html=True)
        st.write("Get health advice based on symptoms.")

        # Feature: Predict Health Advice
        symptoms = st.text_area("Enter your symptoms (comma-separated):")
        if st.button("Predict Health Advice"):
            if symptoms:
                symptom_list = [s.strip() for s in symptoms.split(",")]
                advice = predict_health_advice(symptom_list)
                st.write("## Health Advice")
                st.write(advice)
            else:
                st.error("Please enter symptoms to get advice.")

        # Feature: Find Hospitals
        location = st.text_input("Enter your location to find nearby hospitals:")
        if st.button("Find Hospitals"):
            if location:
                coordinates = geocode_place(location)
                if coordinates:
                    lat, lon = coordinates
                    hospitals = fetch_hospitals(lat, lon)
                    if hospitals:
                        st.write("### Nearby Hospitals")
                        hospital_map = display_map_with_routes(lat, lon, hospitals)
                        folium_static(hospital_map)
                    else:
                        st.error("No hospitals found nearby.")
                else:
                    st.error("Could not find the specified location. Please try again.")
            else:
                st.error("Please enter a location to search for hospitals.")
else:
    st.error("Please log in to access the application.")

# Logout Button
if st.session_state.authenticated:
    if st.sidebar.button("Logout"):
        st.session_state.authenticated = False
        st.session_state.username = None
        st.session_state.hide_login = False
        st.success("Logged out successfully.")
