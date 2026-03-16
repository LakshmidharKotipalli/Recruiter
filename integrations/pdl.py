import logging
from integrations.base import BaseIntegration
from schemas.models import SourceQueries, RawProfile, Experience, Education

logger = logging.getLogger(__name__)


class PDLIntegration(BaseIntegration):
    """People Data Labs integration — enriched professional graph API.

    PDL profiles tend to have more complete data: email and phone are typically present.
    """

    async def fetch(self, queries: SourceQueries) -> list[RawProfile]:
        """Fetch profiles via PDL API or return mock data."""
        if self.mock_mode or self.api_key == "mock-key":
            logger.info("PDL: returning mock profiles")
            return self._mock_profiles()

        raise NotImplementedError("Real PDL integration not yet implemented. Set mock_mode=True.")

    def _mock_profiles(self) -> list[RawProfile]:
        """Return 8 varied mock PDL profiles with global locations.

        PDL profiles have more complete data (email + phone present).
        Includes 2-3 deliberate overlaps with google_cse/naukri for dedup testing.
        """
        return [
            # --- DUPLICATE of google_cse "Priya Nair" — same email, richer PDL data ---
            RawProfile(
                name="Priya Nair",
                title="Senior Software Engineer",
                company="Razorpay",
                skills=["Python", "Django", "FastAPI", "PostgreSQL", "Docker", "AWS", "Microservices", "Redis", "REST APIs"],
                experience=[
                    Experience(title="Senior Software Engineer", company="Razorpay", start_date="2020-01", end_date=None,
                               description="Led migration of monolith to microservices using FastAPI and Docker."),
                    Experience(title="Software Engineer", company="Freshworks", start_date="2017-07", end_date="2019-12"),
                ],
                education=[Education(institution="NIT Trichy", degree="B.E.", field="Computer Science", year=2017)],
                source="pdl",
                profile_url=None,
                location="Bangalore, India",
                email="priya.nair@gmail.com",  # P1 match — will dedup
                phone="+91-9123456789",
            ),
            # --- STRONG MATCH: Singapore-based ---
            RawProfile(
                name="Wei Chen",
                title="Senior Python Engineer",
                company="Grab",
                skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "AWS", "Redis", "Celery", "REST APIs"],
                experience=[
                    Experience(title="Senior Python Engineer", company="Grab", start_date="2019-03", end_date=None,
                               description="Built payment microservices handling 1M+ transactions/day."),
                    Experience(title="Software Engineer", company="Sea Group", start_date="2016-08", end_date="2019-02"),
                ],
                education=[Education(institution="NUS", degree="B.Comp.", field="Computer Science", year=2016)],
                source="pdl",
                profile_url=None,
                location="Singapore",
                email="wei.chen@grab.com",
                phone="+65-91234567",
            ),
            # --- STRONG MATCH: US-based ---
            RawProfile(
                name="Aditya Bose",
                title="Senior Backend Engineer",
                company="Stripe",
                skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "AWS", "GraphQL", "Celery", "Redis"],
                experience=[
                    Experience(title="Senior Backend Engineer", company="Stripe", start_date="2021-05", end_date=None,
                               description="Core payments infrastructure team, Python/FastAPI services."),
                    Experience(title="Backend Engineer", company="Plaid", start_date="2018-09", end_date="2021-04"),
                ],
                education=[Education(institution="Carnegie Mellon University", degree="M.S.", field="CS", year=2018)],
                source="pdl",
                profile_url=None,
                location="San Francisco, CA",
                email="aditya.bose@stripe.com",
                phone="+1-415-555-0101",
            ),
            # --- DUPLICATE of google_cse "Arjun Sharma" — same name + company ---
            RawProfile(
                name="Arjun Sharma",
                title="Senior Python Engineer",
                company="Flipkart",
                skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "AWS", "Redis", "Celery", "Kafka"],
                experience=[
                    Experience(title="Senior Python Engineer", company="Flipkart", start_date="2021-03", end_date=None,
                               description="Platform engineering team, scalable Python APIs."),
                    Experience(title="Python Developer", company="Myntra", start_date="2018-06", end_date="2021-02"),
                ],
                education=[Education(institution="IIT Bombay", degree="B.Tech", field="Computer Science", year=2018)],
                source="pdl",
                profile_url=None,
                location="Bangalore, India",
                email="arjun.sharma@flipkart.com",  # richer data
                phone="+91-9871234567",
            ),
            # --- GOOD MATCH: London-based ---
            RawProfile(
                name="Aisha Rahman",
                title="Senior Python Developer",
                company="Revolut",
                skills=["Python", "FastAPI", "PostgreSQL", "Docker", "AWS", "Redis", "REST APIs", "Microservices"],
                experience=[
                    Experience(title="Senior Python Developer", company="Revolut", start_date="2020-07", end_date=None),
                    Experience(title="Python Engineer", company="Monzo", start_date="2018-03", end_date="2020-06"),
                ],
                education=[Education(institution="UCL", degree="M.Sc.", field="Software Engineering", year=2018)],
                source="pdl",
                profile_url=None,
                location="London, UK",
                email="aisha.r@revolut.com",
                phone="+44-7911-123456",
            ),
            # --- BORDERLINE: Golang primary, some Python ---
            RawProfile(
                name="James O'Brien",
                title="Senior Software Engineer",
                company="Cloudflare",
                skills=["Go", "Python", "Rust", "Docker", "Kubernetes", "AWS", "PostgreSQL"],
                experience=[
                    Experience(title="Senior Software Engineer", company="Cloudflare", start_date="2020-02", end_date=None),
                    Experience(title="Software Engineer", company="Fastly", start_date="2017-06", end_date="2020-01"),
                ],
                education=[Education(institution="Trinity College Dublin", degree="B.Sc.", field="CS", year=2017)],
                source="pdl",
                profile_url=None,
                location="Dublin, Ireland",
                email="james.obrien@cloudflare.com",
                phone="+353-87-1234567",
            ),
            # --- GOOD MATCH: Toronto-based ---
            RawProfile(
                name="Fatima Al-Hassan",
                title="Senior Python Engineer",
                company="Shopify",
                skills=["Python", "FastAPI", "Django", "PostgreSQL", "Docker", "AWS", "Celery", "REST APIs"],
                experience=[
                    Experience(title="Senior Python Engineer", company="Shopify", start_date="2019-09", end_date=None,
                               description="Merchant platform APIs, high-throughput Python services."),
                    Experience(title="Python Developer", company="Wave HQ", start_date="2017-01", end_date="2019-08"),
                ],
                education=[Education(institution="University of Toronto", degree="B.Sc.", field="Computer Science", year=2017)],
                source="pdl",
                profile_url=None,
                location="Toronto, Canada",
                email="fatima.alhassan@shopify.com",
                phone="+1-647-555-0202",
            ),
            # --- POOR MATCH: DevOps/SRE focus ---
            RawProfile(
                name="Rahul Kapoor",
                title="Senior DevOps Engineer",
                company="Amazon Web Services",
                skills=["Terraform", "Kubernetes", "Docker", "AWS", "Python", "Ansible", "Jenkins"],
                experience=[
                    Experience(title="Senior DevOps Engineer", company="Amazon Web Services", start_date="2019-06", end_date=None),
                    Experience(title="DevOps Engineer", company="Infosys", start_date="2016-03", end_date="2019-05"),
                ],
                education=[Education(institution="NIT Surathkal", degree="B.Tech", field="CS", year=2016)],
                source="pdl",
                profile_url=None,
                location="Bangalore, India",
                email="rahul.kapoor.devops@gmail.com",
                phone="+91-9654321098",
            ),
        ]
