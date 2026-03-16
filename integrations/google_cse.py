import logging
from integrations.base import BaseIntegration
from schemas.models import SourceQueries, RawProfile, Experience, Education

logger = logging.getLogger(__name__)


class GoogleCSEIntegration(BaseIntegration):
    """Google Custom Search Engine integration for LinkedIn public profiles."""

    async def fetch(self, queries: SourceQueries) -> list[RawProfile]:
        """Fetch profiles via Google CSE or return mock data."""
        if self.mock_mode or self.api_key == "mock-key":
            logger.info("GoogleCSE: returning mock profiles")
            return self._mock_profiles()

        # Real implementation would iterate over queries.primary (and fallback on 0 results)
        # and call the Google Custom Search JSON API
        raise NotImplementedError("Real Google CSE integration not yet implemented. Set mock_mode=True.")

    def _mock_profiles(self) -> list[RawProfile]:
        """Return 8 varied mock LinkedIn profiles for a 'Senior Python Engineer' search."""
        return [
            # --- STRONG MATCH ---
            RawProfile(
                name="Arjun Sharma",
                title="Senior Python Engineer",
                company="Flipkart",
                skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "AWS", "Redis", "Celery"],
                experience=[
                    Experience(title="Senior Python Engineer", company="Flipkart", start_date="2021-03", end_date=None),
                    Experience(title="Python Developer", company="Myntra", start_date="2018-06", end_date="2021-02"),
                ],
                education=[Education(institution="IIT Bombay", degree="B.Tech", field="Computer Science", year=2018)],
                source="google_cse",
                profile_url=None,
                location="Bangalore, India",
            ),
            # --- STRONG MATCH (will be deduped with PDL) ---
            RawProfile(
                name="Priya Nair",
                title="Senior Software Engineer",
                company="Razorpay",
                skills=["Python", "Django", "FastAPI", "PostgreSQL", "Docker", "AWS", "Microservices"],
                experience=[
                    Experience(title="Senior Software Engineer", company="Razorpay", start_date="2020-01", end_date=None),
                    Experience(title="Software Engineer", company="Freshworks", start_date="2017-07", end_date="2019-12"),
                ],
                education=[Education(institution="NIT Trichy", degree="B.E.", field="Computer Science", year=2017)],
                source="google_cse",
                profile_url=None,
                location="Bangalore, India",
                email="priya.nair@gmail.com",
            ),
            # --- GOOD MATCH (missing Kubernetes/REST APIs nice-to-haves) ---
            RawProfile(
                name="Rohan Mehta",
                title="Backend Engineer",
                company="Swiggy",
                skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Redis", "REST APIs"],
                experience=[
                    Experience(title="Backend Engineer", company="Swiggy", start_date="2019-06", end_date=None),
                    Experience(title="Junior Developer", company="TechCorp", start_date="2017-08", end_date="2019-05"),
                ],
                education=[Education(institution="BITS Pilani", degree="B.E.", field="Computer Science", year=2017)],
                source="google_cse",
                profile_url=None,
                location="Bangalore, India",
            ),
            # --- BORDERLINE: mid-level, missing some must-haves ---
            RawProfile(
                name="Sneha Reddy",
                title="Python Developer",
                company="Infosys",
                skills=["Python", "Django", "MySQL", "REST APIs", "Git"],
                experience=[
                    Experience(title="Python Developer", company="Infosys", start_date="2020-07", end_date=None),
                ],
                education=[Education(institution="Osmania University", degree="B.Tech", field="IT", year=2020)],
                source="google_cse",
                profile_url=None,
                location="Hyderabad, India",
            ),
            # --- LEAD-LEVEL: over-qualified ---
            RawProfile(
                name="Vikram Patel",
                title="Lead Platform Engineer",
                company="Ola",
                skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "AWS", "Terraform", "Go"],
                experience=[
                    Experience(title="Lead Platform Engineer", company="Ola", start_date="2019-01", end_date=None),
                    Experience(title="Senior Engineer", company="Paytm", start_date="2015-06", end_date="2018-12"),
                ],
                education=[Education(institution="IIT Delhi", degree="B.Tech", field="CSE", year=2015)],
                source="google_cse",
                profile_url=None,
                location="Bangalore, India",
            ),
            # --- POOR MATCH: React/frontend focus ---
            RawProfile(
                name="Neha Gupta",
                title="Full Stack Developer",
                company="Zomato",
                skills=["React", "JavaScript", "Node.js", "Python", "MongoDB"],
                experience=[
                    Experience(title="Full Stack Developer", company="Zomato", start_date="2021-02", end_date=None),
                ],
                education=[Education(institution="Delhi University", degree="B.Sc.", field="Computer Science", year=2021)],
                source="google_cse",
                profile_url=None,
                location="Delhi, India",
            ),
            # --- WILL BE DEDUPED WITH NAUKRI ---
            RawProfile(
                name="Karthik Rajan",
                title="Senior Python Developer",
                company="Zoho Corporation",
                skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Celery", "Redis", "AWS"],
                experience=[
                    Experience(title="Senior Python Developer", company="Zoho Corporation", start_date="2018-09", end_date=None),
                    Experience(title="Software Engineer", company="HCL Technologies", start_date="2015-06", end_date="2018-08"),
                ],
                education=[Education(institution="Anna University", degree="B.E.", field="CSE", year=2015)],
                source="google_cse",
                profile_url=None,
                location="Chennai, India",
                email="karthik.rajan@gmail.com",
            ),
            # --- JUNIOR: clearly under-qualified ---
            RawProfile(
                name="Ananya Joshi",
                title="Junior Python Developer",
                company="StartupXYZ",
                skills=["Python", "Flask", "SQLite", "HTML", "CSS"],
                experience=[
                    Experience(title="Junior Python Developer", company="StartupXYZ", start_date="2023-06", end_date=None),
                ],
                education=[Education(institution="Pune University", degree="B.Tech", field="IT", year=2023)],
                source="google_cse",
                profile_url=None,
                location="Pune, India",
            ),
        ]
