FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy
RUN apt-get update && apt-get install -y ffmpeg

# 1. Create a main folder inside the container
WORKDIR /app

# 2. Copy your ENTIRE VS Code workspace into the container
COPY . /app

# 3. Now, explicitly "walk" into the folder where app.py lives!
WORKDIR /app/project/project/ISSP

# 4. Install the Python libraries
RUN pip install --no-cache-dir -r requirements.txt

# 5. Run Gunicorn (it is now standing right next to app.py)
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]