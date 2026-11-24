
pipeline {
    agent any

    environment {
        PYTHON = 'C:\\Users\\Avishkar\\AppData\\Local\\Programs\\Python\\Python311\\python.exe'
    }

    stages {
        stage('Checkout') {
            steps {
                echo 'GitHub checkout completed successfully!'
            }
        }

        stage('Python Version') {
            steps {
                bat '"%PYTHON%" --version'
                bat '"%PYTHON%" -m pip --version'
            }
        }

        stage('Create Virtual Environment') {
            steps {
                bat '"%PYTHON%" -m venv .venv'
            }
        }

        stage('Install Dependencies') {
            steps {
                bat '.venv\\Scripts\\python.exe -m pip install --upgrade pip'
                bat '.venv\\Scripts\\python.exe -m pip install -r requirements.txt'
            }
        }

        stage('Run Tests') {
            steps {
                withCredentials([string(
                    credentialsId: 'DATABASE_URL',
                    variable: 'DATABASE_URL'
                )]) {
                    bat '.venv\\Scripts\\python.exe -m pytest -q'
                }
            }
        }
    }
}
