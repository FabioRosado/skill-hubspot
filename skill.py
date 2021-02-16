"""A skill for opsdroid that integrates with HubSpot."""
import aiohttp
import json
import logging

from voluptuous import Required

from opsdroid.skill import Skill
from opsdroid import events
from opsdroid.matchers import match_event, match_regex
from opsdroid.connector.github.events import (
    IssueCreated,
    IssueClosed,

)


_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = {
    Required("token"): str
}
HUBSPOT_API_URL= "https://api.hubapi.com/crm/v3/"


class HubspotSkill(Skill):
    """An Opsdroid skill to integrate opsdroid with Hubspot."""

    def __init__(self, opsdroid, config, *args, **kwargs):
        super().__init__(opsdroid, config, *args, **kwargs)
        self.token = config.get('token') 
        self.contacts =  {}
    
    async def query_api(self, endpoint, method="GET", **params):
        """Query a HubSpot API endpoint.

        This is an helper method that makes calling the api and getting a
        response back easy. This method is based on the Home Assistant plugin
        for opsdroid.

        Args:
            endpoint: The endpoint that comes after ``HUBSPOT_API_URL``
            method: HTTP method to use (GET/POST)
            **params: Parameters are specified as kwargs
                For GET requests there will be sent as url params
                For POST requests these will be used as the post body.

        """
        url = f"{HUBSPOT_API_URL}{endpoint}?hapikey={self.token}"
        headers = {
            'accept': "application/json",
            'content-type': "application/json"
        }

        response = None
        async with aiohttp.ClientSession() as session:

            if method.upper() == "GET":
                async with session.get(url=url, headers=headers, params=params) as resp:
                    if resp.status >= 400:
                        _LOGGER.error("Error when calling HubSpot API - %s - %s", resp.status, await resp.text())
                        return None
                    else:
                        response = await resp.json()

            if method.upper() == "POST":
                _LOGGER.info(json.dumps(params))
                async with session.post(url=url,headers=headers, data=json.dumps(params)
                ) as resp:
                    if resp.status >= 400:
                        _LOGGER.error("Error when calling HubSpot API - %s - %s", resp.status, resp)
                        return None
                    else:
                        response = await resp.json()
            
            if method.upper() == "PATCH":
                _LOGGER.info(json.dumps(params))
                async with session.patch(url=url, headers=headers, data=json.dumps(params)) as resp:
                    if resp.status >= 400:
                        _LOGGER.error("Error when calling HubSpot API - %s - %s", resp.status, resp)
                        return None
                    else:
                        response = await resp.json()

        return response
    
    async def get_contact_details_from_github(self, username):
        """Call the GitHub API to get more information from user.

        We should avoid creating contacts in HubSpot from GitHub usernames,
        this method will call the ``users`` enpoint to get more information
        from the user that created the issue. Note that this information is
        public avalaible, which means that some users might not have their 
        First/Last name and email available - if this is the case we will
        just default to creating a contact with the GitHub username instead.

        Args:
            username: The username to use when querying the GitHub Api

        """
        async with aiohttp.ClientSession() as session:
            request = await session.get(f"https://api.github.com/users/{username}")
            user_info = await request.json()

            contact_info = {username: {}}

            if user_info["name"]:
                name = user_info["name"].split(" ") 

                # Note: 'firstname' and 'lastname' are expected by hubspot api
                contact_info[username]["firstname"] = name[0]
                contact_info[username]["lastname"] = name[-1] # Assuming that the user has at least two names
            
            if user_info["email"]:
                contact_info[username]["email"] = user_info["email"]
            
            if user_info["blog"]:
                contact_info[username]["website"] = user_info["blog"]
            
            if user_info["company"]:
                contact_info[username]["company"] = user_info["company"]

            return contact_info

    async def create_contact(self, username):
        """Create a contact from GitHub.
        
        If the contact doesn't exist in the database, there's a good chance
        that the contact doesn't exist in the HubSpot. If that's the case then
        we should create the contact and add it to the database.

        Return:
            contact information (dict)

        """
        github_contact_info = await self.get_contact_details_from_github(username)

        payload = {"properties": github_contact_info[username]}

        resp = await self.query_api("objects/contacts", "POST", properties=payload["properties"])

        # Add hubspot_id to the contact info once we get it
        github_contact_info[username]["hubspot_id"] = resp["id"]

        self.contacts[username] = github_contact_info[username] # this will be a dict of dicts: {username: {"first name": .., "last name": ..}}

        _LOGGER.debug(f"Creating contact '{username}' - {self.contacts}")

        # Put the whole list of contacts into the db.
        await self.opsdroid.memory.put("contacts", self.contacts)

        return github_contact_info
    
    async def put_ticket_reference_in_db(self, title, id, user):
        """Put ticket reference in database.

        This is a helper method to put a reference of a created ticket
        into the database. We need to do this because we need to use the
        ``id`` of the ticket if we want to update the stage, add notes or
        close a ticket.

        With this method we will put the ``title``, ``id`` and ``user`` which
        allows us to use the title to update a ticket in the HubSpot.

        """
        tickets = await self.opsdroid.memory.get("tickets")

        if not tickets:
            tickets = {}

        tickets[title] = {"id": id, "user": user}

        _LOGGER.debug(f"Putting {title} into the tickets database")

        await self.opsdroid.memory.put("tickets", tickets)

        tickets = None


    @match_event(IssueCreated)
    async def create_ticket(self, event):
        """Create a ticket from a Github issue.
        
        When creating a ticket from the API we have to include the ``hs_pipline_stage`` 
        and ``hs_pipeline`. The stage is where we should put the ticket (New, Waiting on 
        contact, waiting on us, closed). The pipeline will be the support pipline. Note that
        the API expects you to pass the internal id of these properties, you can get them by going 
        to the ``settings > Tickets`` then click on the ``</>`` to see the internal id of the
        pipeline and stage.

        """
        _LOGGER.debug(f"Received 'IssueCreated' event with title '{event.title}'.")
        payload = {"properties": {"subject": event.title, "content": event.description, "hs_pipeline_stage": 1,"hs_pipeline": 0, "hs_ticket_priority": "LOW" }}
        resp = await self.query_api("objects/tickets", "POST", properties=payload["properties"])

        await self.put_ticket_reference_in_db(title=event.title, user=event.user, id=resp["id"])

        self.contacts = await self.opsdroid.memory.get("contacts")
        if not self.contacts:
            self.contacts = {}

        if self.contacts and event.user in self.contacts.keys():
            await self.associate_ticket_to_contact(resp["id"], self.contacts[event.user]['hubspot_id'])
        else:
            _LOGGER.debug(f"Contact '{event.user}' not found creating...")
            contact_info = await self.create_contact(event.user)
            _LOGGER.debug(f"Created contact {contact_info}")
            if contact_info:
                await self.associate_ticket_to_contact(resp["id"], contact_info[event.user]["hubspot_id"])

    async def associate_ticket_to_contact(self, ticket_id, contact_id):
        """Associate ticket to contact.

        With HubSpot we need to associate tickets to contacts by calling
        another API endpoint. This method will first check if we have the
        user id in the database, if we do then we will just create the association
        if we don't then we will call the GitHub api to query the user endpoint - this
        will allow us to get information like the user full name and email. Note that a
        user might not have the full name or email public.

        Args:
            ticket_id: The id of the ticket to associate the contact with
            contact_id: The id of the contact to assiciate the ticket with

        """
        payload = {
            "inputs": [
                {
                    "from": {
                        "id": str(ticket_id)
                    },
                    "to": {
                        "id": str(contact_id)
                    },
                    "type": "ticket_to_contact"
                } 
            ]
        }

        resp = await self.query_api("associations/ticket/contact/batch/create", "POST", inputs=payload["inputs"])

        if resp:
            _LOGGER.debug(f"Associated contact '{contact_id}' to ticket '{ticket_id}'.")

    
    @match_event(IssueClosed)
    async def close_ticket(self, event):
        """Close a ticker when we close a github issue.
        
        The HubSpot api doesn't have a way to close tickets because "CLOSED" is just a status. 
        Here we can do two things, either PATCH the ticket with a new status of closed or
        we can delete the ticket - we probably don't want to do this as we want to keep track of
        a user's history with us.

        """
        tickets = await self.opsdroid.memory.get("tickets")

        ticket = tickets.pop(event.title, None)
        _LOGGER.info(f"Got ticket from the 'db': {ticket}")

        if ticket:
            payload = {"properties": {"hs_pipeline_stage": 4}}
            resp = await self.query_api(f"objects/tickets/{ticket['id']}", "PATCH", properties=payload["properties"])

            if resp:
                # Update the db without the closed ticket
                await self.opsdroid.memory.put("tickets", tickets)
                tickets = None

    
    async def create_note(self):
        """Create a note on the ticket from a Github comment."""
    
     