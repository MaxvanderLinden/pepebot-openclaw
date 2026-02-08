#!/usr/bin/env python3
"""
Flight search with Pydantic validation.
Supports Skyscanner, Google Flights, and Booking.com via Brave Search API.
"""

import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

import sys
import json
import time
import requests
import re
from typing import Optional, List, Literal
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ConfigDict
from enum import Enum


class WebsiteName(str, Enum):
    """Supported flight booking websites."""
    SKYSCANNER = "skyscanner"
    GOOGLE = "google"
    BOOKING = "booking"
    COMPARE = "compare"


class WebsiteConfig(BaseModel):
    """Configuration for a flight booking website."""
    domain: str = Field(..., description="Domain to search")
    name: str = Field(..., description="Display name")
    query_template: str = Field(..., description="Search query template")

    model_config = ConfigDict(frozen=True)


class FlightSearchParams(BaseModel):
    """Parameters for flight search."""
    origin: str = Field(..., min_length=3, max_length=3, description="Origin airport code (e.g., JFK)")
    destination: str = Field(..., min_length=3, max_length=3, description="Destination airport code (e.g., LHR)")
    depart_date: str = Field(..., description="Departure date in YYYY-MM-DD format")
    website: WebsiteName = Field(default=WebsiteName.SKYSCANNER, description="Website to search")

    @field_validator('origin', 'destination')
    @classmethod
    def validate_airport_code(cls, v: str) -> str:
        """Validate and normalize airport codes."""
        v = v.strip().upper()
        if not re.match(r'^[A-Z]{3}$', v):
            raise ValueError(f"Invalid airport code: {v}. Must be 3 letters (e.g., JFK)")
        return v

    @field_validator('depart_date')
    @classmethod
    def validate_date(cls, v: str) -> str:
        """Validate date format and ensure it's not in the past."""
        try:
            date_obj = datetime.strptime(v, '%Y-%m-%d')
        except ValueError:
            raise ValueError(f"Invalid date format: {v}. Must be YYYY-MM-DD (e.g., 2026-05-15)")

        if date_obj.date() < datetime.now().date():
            raise ValueError(f"Date cannot be in the past: {v}")

        return v

    model_config = ConfigDict(frozen=True)


class FlightResult(BaseModel):
    """Individual flight search result."""
    title: str = Field(..., description="Result title")
    url: str = Field(..., description="Booking URL")
    description: str = Field(..., description="Result description")
    price: Optional[str] = Field(None, description="Extracted price (e.g., $485)")
    website: str = Field(..., description="Source website name")

    @field_validator('url')
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Ensure URL is valid."""
        if not v.startswith(('http://', 'https://')):
            raise ValueError(f"Invalid URL: {v}")
        return v


class SearchResponse(BaseModel):
    """Response from a single website search."""
    website: str = Field(..., description="Website that was searched")
    query: str = Field(..., description="Search query used")
    results: List[FlightResult] = Field(default_factory=list, description="List of flight results")
    cheapest: Optional[FlightResult] = Field(None, description="Cheapest flight found")
    count: int = Field(..., ge=0, description="Number of results found")


class ComparisonItem(BaseModel):
    """Price comparison for a single website."""
    website: str = Field(..., description="Website name")
    cheapest_price: str = Field(..., description="Cheapest price on this site")
    url: str = Field(..., description="URL to cheapest option")
    is_best: bool = Field(..., description="Whether this is the overall best price")


class BestDeal(BaseModel):
    """Best deal found across all sites."""
    website: str = Field(..., description="Website name")
    price: str = Field(..., description="Best price found")
    url: str = Field(..., description="URL to the best deal")
    title: str = Field(..., description="Result title")


class ComparisonResponse(BaseModel):
    """Response from comparing multiple websites."""
    mode: Literal["comparison"] = Field(default="comparison", description="Response mode")
    sites_checked: int = Field(..., ge=0, le=3, description="Number of sites checked")
    comparison: List[ComparisonItem] = Field(..., description="Price comparison across sites")
    best_deal: BestDeal = Field(..., description="Best deal found overall")
    all_results: List[SearchResponse] = Field(..., description="Full results from all sites")


class ErrorResponse(BaseModel):
    """Error response."""
    error: str = Field(..., description="Error message")
    suggestion: Optional[str] = Field(None, description="Suggestion to fix the error")
    sites_checked: Optional[List[str]] = Field(None, description="Sites that were checked")


# Website configurations
WEBSITES: dict[WebsiteName, WebsiteConfig] = {
    WebsiteName.SKYSCANNER: WebsiteConfig(
        domain='skyscanner.com',
        name='Skyscanner',
        query_template='site:skyscanner.com direct flights {origin} to {destination} {date}'
    ),
    WebsiteName.GOOGLE: WebsiteConfig(
        domain='google.com/travel/flights',
        name='Google Flights',
        query_template='site:google.com/travel/flights direct flights {origin} to {destination} {date}'
    ),
    WebsiteName.BOOKING: WebsiteConfig(
        domain='booking.com/flights',
        name='Booking.com',
        query_template='site:booking.com/flights direct {origin} to {destination} {date}'
    )
}


class FlightSearcher:
    """Flight search service using Brave Search API."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the flight searcher.
        
        Args:
            api_key: Brave Search API key. If None, uses BRAVE_API_KEY env var.
        """
        self.api_key = api_key or os.environ.get('BRAVE_API_KEY')
        if not self.api_key:
            raise ValueError("BRAVE_API_KEY must be set in environment or passed as argument")
        
        self.base_url = "https://api.search.brave.com/res/v1/web/search"
        self.timeout = 30

    def search_flights(self, params: FlightSearchParams) -> SearchResponse | ErrorResponse:
        """
        Search for flights on a specific website.
        
        Args:
            params: Flight search parameters
            
        Returns:
            SearchResponse with results or ErrorResponse on failure
        """
        if params.website == WebsiteName.COMPARE:
            return ErrorResponse(
                error="Use search_all_sites() for comparison mode",
                suggestion="Call search_all_sites() instead of search_flights()"
            )

        site_config = WEBSITES[params.website]
        
        # Build site-specific query
        query = site_config.query_template.format(
            origin=params.origin,
            destination=params.destination,
            date=params.depart_date
        )
        
        try:
            response = requests.get(
                self.base_url,
                headers={
                    "X-Subscription-Token": self.api_key,
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip"
                },
                params={
                    "q": query,
                    "count": 10,
                    "search_lang": "en"
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
            
            # Parse results
            results = self._parse_results(data, site_config)
            
            # Sort by price
            results_with_price = [r for r in results if r.price]
            results_without_price = [r for r in results if not r.price]
            
            results_with_price.sort(key=lambda x: self._parse_price(x.price))
            
            final_results = (results_with_price + results_without_price)[:10]
            
            return SearchResponse(
                website=site_config.name,
                query=query,
                results=final_results,
                cheapest=results_with_price[0] if results_with_price else None,
                count=len(final_results)
            )
            
        except requests.exceptions.Timeout:
            return ErrorResponse(
                error=f"Search request timed out after {self.timeout} seconds",
                suggestion="Try again or check your internet connection"
            )
        except requests.exceptions.RequestException as e:
            return ErrorResponse(
                error=f"Network error: {str(e)}",
                suggestion="Check your internet connection and API key"
            )
        except Exception as e:
            return ErrorResponse(
                error=f"Unexpected error: {str(e)}",
                suggestion="Contact support if this persists"
            )

    def search_all_sites(self, params: FlightSearchParams) -> ComparisonResponse | ErrorResponse:
        """
        Search all three sites and compare prices.
        
        Args:
            params: Flight search parameters (website field is ignored)
            
        Returns:
            ComparisonResponse with comparison or ErrorResponse on failure
        """
        sites_to_check = [WebsiteName.SKYSCANNER, WebsiteName.GOOGLE, WebsiteName.BOOKING]
        all_results: List[SearchResponse] = []
        
        for i, site in enumerate(sites_to_check):
            if i > 0:
                time.sleep(0.5)
            search_params = FlightSearchParams(
                origin=params.origin,
                destination=params.destination,
                depart_date=params.depart_date,
                website=site
            )
            result = self.search_flights(search_params)

            if isinstance(result, SearchResponse) and result.cheapest:
                all_results.append(result)
        
        if not all_results:
            return ErrorResponse(
                error="No results found on any of the three sites",
                sites_checked=["Skyscanner", "Google Flights", "Booking.com"],
                suggestion="Try different dates or check the sites directly"
            )
        
        # Find overall cheapest
        best_deal = min(all_results, key=lambda x: self._parse_price(x.cheapest.price))
        
        # Create comparison summary
        comparison_summary: List[ComparisonItem] = []
        for result in all_results:
            if result.cheapest:
                comparison_summary.append(ComparisonItem(
                    website=result.website,
                    cheapest_price=result.cheapest.price,
                    url=result.cheapest.url,
                    is_best=result.website == best_deal.website
                ))
        
        # Sort by price
        comparison_summary.sort(key=lambda x: self._parse_price(x.cheapest_price))
        
        return ComparisonResponse(
            mode="comparison",
            sites_checked=len(all_results),
            comparison=comparison_summary,
            best_deal=BestDeal(
                website=best_deal.website,
                price=best_deal.cheapest.price,
                url=best_deal.cheapest.url,
                title=best_deal.cheapest.title
            ),
            all_results=all_results
        )

    def _parse_results(self, data: dict, site_config: WebsiteConfig) -> List[FlightResult]:
        """Parse search API response into FlightResult objects."""
        results: List[FlightResult] = []
        
        for item in data.get('web', {}).get('results', []):
            title = item.get('title', '')
            description = item.get('description', '')
            url = item.get('url', '')
            
            if not url:
                continue
            
            # Extract price
            price = self._extract_price(title + ' ' + description)
            
            # Verify result is from target domain
            domain_match = site_config.domain in url.lower()
            
            if domain_match:  # Only include results from the target domain
                # Truncate long descriptions
                truncated_desc = description[:200] + '...' if len(description) > 200 else description
                
                try:
                    results.append(FlightResult(
                        title=title,
                        url=url,
                        description=truncated_desc,
                        price=price,
                        website=site_config.name
                    ))
                except Exception as e:
                    # Skip invalid results
                    print(f"Skipping invalid result: {e}", file=sys.stderr)
                    continue
        
        return results

    @staticmethod
    def _extract_price(text: str) -> Optional[str]:
        """Extract price from text - supports $, £, €."""
        patterns = [
            r'\$[\d,]+(?:\.\d{2})?',  # $485 or $1,234.56
            r'£[\d,]+(?:\.\d{2})?',   # £485
            r'€[\d,]+(?:\.\d{2})?',   # €485
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        return None

    @staticmethod
    def _parse_price(price_str: Optional[str]) -> float:
        """Convert price string to float for sorting."""
        if not price_str:
            return float('inf')
        
        # Remove currency symbols and commas
        clean = re.sub(r'[$£€,]', '', price_str)
        try:
            return float(clean)
        except ValueError:
            return float('inf')


def main():
    """CLI entry point."""
    if len(sys.argv) < 4:
        usage = {
            "error": "Invalid usage",
            "usage": "python flight_search.py <origin> <destination> <date> [website]",
            "examples": [
                "python flight_search.py JFK LHR 2026-05-15 skyscanner",
                "python flight_search.py LAX NRT 2026-06-01 google",
                "python flight_search.py BOS CDG 2026-07-10 booking",
                "python flight_search.py JFK LHR 2026-05-15 compare"
            ],
            "websites": {
                "skyscanner": "Default - best price comparison",
                "google": "Google Flights - good for direct flights",
                "booking": "Booking.com - alternative pricing",
                "compare": "Check all three sites and compare"
            }
        }
        print(json.dumps(usage, indent=2))
        sys.exit(1)
    
    origin = sys.argv[1]
    destination = sys.argv[2]
    depart_date = sys.argv[3]
    website_str = sys.argv[4].lower() if len(sys.argv) > 4 else 'skyscanner'
    
    # Validate website
    try:
        website = WebsiteName(website_str)
    except ValueError:
        error = ErrorResponse(
            error=f"Invalid website: {website_str}",
            suggestion=f"Use one of: {', '.join([w.value for w in WebsiteName])}"
        )
        print(json.dumps(error.model_dump(), indent=2))
        sys.exit(1)
    
    # Create search parameters
    try:
        params = FlightSearchParams(
            origin=origin,
            destination=destination,
            depart_date=depart_date,
            website=website
        )
    except Exception as e:
        error = ErrorResponse(
            error=f"Invalid parameters: {str(e)}",
            suggestion="Check your airport codes (3 letters) and date format (YYYY-MM-DD)"
        )
        print(json.dumps(error.model_dump(), indent=2))
        sys.exit(1)
    
    # Execute search
    try:
        searcher = FlightSearcher()
        
        if website == WebsiteName.COMPARE:
            result = searcher.search_all_sites(params)
        else:
            result = searcher.search_flights(params)
        
        # Output result as JSON
        print(json.dumps(result.model_dump(), indent=2))
        
    except ValueError as e:
        error = ErrorResponse(
            error=str(e),
            suggestion="Make sure BRAVE_API_KEY environment variable is set"
        )
        print(json.dumps(error.model_dump(), indent=2))
        sys.exit(1)
    except Exception as e:
        error = ErrorResponse(
            error=f"Unexpected error: {str(e)}",
            suggestion="Contact support if this persists"
        )
        print(json.dumps(error.model_dump(), indent=2))
        sys.exit(1)


if __name__ == '__main__':
    main()