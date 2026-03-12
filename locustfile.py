from locust import HttpUser, task, between

class WebsiteUser(HttpUser):
    wait_time = between(1, 2)

    @task
    def visit_homepage(self):
        # Step 1: set language via POST
        self.client.post("/setlanguage/hi")

        # Step 2: request homepage that renders Jinja2 template
        self.client.get("/")
