import logging
from integrations.base import BaseIntegration
from schemas.models import SourceQueries, RawProfile, Experience, Education

logger = logging.getLogger(__name__)


class NaukriIntegration(BaseIntegration):
    """Naukri.com partner API integration — India's largest job portal."""

    async def fetch(self, queries: SourceQueries) -> list[RawProfile]:
        """Fetch profiles via Naukri API or return mock data."""
        if self.mock_mode or self.api_key == "mock-key":
            logger.info("Naukri: returning mock profiles")
            return self._mock_profiles()

        raise NotImplementedError("Real Naukri integration not yet implemented. Set mock_mode=True.")

    def _mock_profiles(self) -> list[RawProfile]:
        """Return 8 varied mock Naukri profiles with India-based locations.

        Includes 2-3 profiles that deliberately overlap with Google CSE mock data
        to exercise the deduplication layer.
        """
        return [
            # --- DUPLICATE of google_cse "Karthik Rajan" — same email, slightly different title ---
            RawProfile(
                name="Karthik Rajan",
                title="Senior Python Developer",
                company="Zoho Corporation",
                skills=["Python", "FastAPI", "PostgreSQL", "Docker", "AWS", "Celery", "Django REST Framework"],
                experience=[
                    Experience(title="Senior Python Developer", company="Zoho Corporation", start_date="2018-09", end_date=None),
                    Experience(title="Software Engineer", company="HCL Technologies", start_date="2015-06", end_date="2018-08"),
                ],
                education=[Education(institution="Anna University", degree="B.E.", field="CSE", year=2015)],
                source="naukri",
                profile_url=None,
                location="Chennai, India",
                email="karthik.rajan@gmail.com",  # same email → P1 match with google_cse
                phone="+91-9876543210",
            ),
            # --- STRONG MATCH ---
            RawProfile(
                name="Deepa Krishnan",
                title="Senior Backend Engineer",
                company="PhonePe",
                skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "AWS", "Redis", "REST APIs"],
                experience=[
                    Experience(title="Senior Backend Engineer", company="PhonePe", start_date="2020-04", end_date=None),
                    Experience(title="Backend Developer", company="Wipro", start_date="2017-07", end_date="2020-03"),
                ],
                education=[Education(institution="VIT University", degree="B.Tech", field="CSE", year=2017)],
                source="naukri",
                profile_url=None,
                location="Bangalore, India",
                email="deepa.k@outlook.com",
            ),
            # --- GOOD MATCH ---
            RawProfile(
                name="Suresh Babu",
                title="Senior Python Engineer",
                company="Byju's",
                skills=["Python", "Django", "FastAPI", "PostgreSQL", "Docker", "Celery", "AWS"],
                experience=[
                    Experience(title="Senior Python Engineer", company="Byju's", start_date="2019-02", end_date=None),
                    Experience(title="Python Developer", company="Cognizant", start_date="2016-04", end_date="2019-01"),
                ],
                education=[Education(institution="Bangalore University", degree="B.E.", field="CS", year=2016)],
                source="naukri",
                profile_url=None,
                location="Bangalore, India",
            ),
            # --- DUPLICATE of google_cse "Priya Nair" — same name + company (P2 match) ---
            RawProfile(
                name="Priya Nair",
                title="Senior Software Engineer",
                company="Razorpay",
                skills=["Python", "Django", "PostgreSQL", "Docker", "AWS", "Microservices", "Redis"],
                experience=[
                    Experience(title="Senior Software Engineer", company="Razorpay", start_date="2020-01", end_date=None),
                    Experience(title="Software Engineer", company="Freshworks", start_date="2017-07", end_date="2019-12"),
                ],
                education=[Education(institution="NIT Trichy", degree="B.E.", field="CS", year=2017)],
                source="naukri",
                profile_url=None,
                location="Bangalore, India",
                email="priya.nair@gmail.com",  # same email → P1 dedup
            ),
            # --- MID-LEVEL but strong skills ---
            RawProfile(
                name="Rahul Verma",
                title="Python Developer",
                company="Meesho",
                skills=["Python", "FastAPI", "PostgreSQL", "Docker", "AWS", "REST APIs"],
                experience=[
                    Experience(title="Python Developer", company="Meesho", start_date="2021-01", end_date=None),
                    Experience(title="Associate Developer", company="TCS", start_date="2019-06", end_date="2020-12"),
                ],
                education=[Education(institution="JNTU Hyderabad", degree="B.Tech", field="IT", year=2019)],
                source="naukri",
                profile_url=None,
                location="Hyderabad, India",
            ),
            # --- GOOD MATCH: Mumbai-based ---
            RawProfile(
                name="Anjali Shah",
                title="Senior Python Developer",
                company="HDFC Securities",
                skills=["Python", "FastAPI", "Django", "PostgreSQL", "Docker", "Redis", "Celery", "AWS"],
                experience=[
                    Experience(title="Senior Python Developer", company="HDFC Securities", start_date="2018-11", end_date=None),
                    Experience(title="Python Engineer", company="Tata Consultancy Services", start_date="2016-06", end_date="2018-10"),
                ],
                education=[Education(institution="Mumbai University", degree="B.E.", field="Computer Engineering", year=2016)],
                source="naukri",
                profile_url=None,
                location="Mumbai, India",
                phone="+91-9988776655",
            ),
            # --- BORDERLINE: good Python skills, missing Docker/FastAPI ---
            RawProfile(
                name="Sanjay Kumar",
                title="Python Backend Developer",
                company="Wipro Limited",
                skills=["Python", "Django", "MySQL", "REST APIs", "Git", "Linux", "Jenkins"],
                experience=[
                    Experience(title="Python Backend Developer", company="Wipro Limited", start_date="2018-08", end_date=None),
                    Experience(title="Software Trainee", company="Wipro Limited", start_date="2017-06", end_date="2018-07"),
                ],
                education=[Education(institution="SRM University", degree="B.Tech", field="CSE", year=2017)],
                source="naukri",
                profile_url=None,
                location="Chennai, India",
            ),
            # --- LEAD-LEVEL from Pune ---
            RawProfile(
                name="Meghna Desai",
                title="Technical Lead - Python",
                company="Persistent Systems",
                skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "AWS", "GraphQL", "Terraform"],
                experience=[
                    Experience(title="Technical Lead", company="Persistent Systems", start_date="2017-09", end_date=None),
                    Experience(title="Senior Engineer", company="Infosys", start_date="2013-06", end_date="2017-08"),
                ],
                education=[Education(institution="Pune University", degree="B.E.", field="Computer Engineering", year=2013)],
                source="naukri",
                profile_url=None,
                location="Pune, India",
                email="meghna.desai@gmail.com",
            ),
        ]
