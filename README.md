# TweetDelete / XTweetDelete

*I call the app TweetDelete but there is another web based service called https://tweetdelete.net/ that has registered the name on X. Hence you may see XTweetDelete used for this desktop app in places to avoid namespace conflicts, e.g., X’s developer console and authorisation process.*

## What does TweetDelete do?
TweetDelete will delete your posts, replies, likes & reposts from X in bulk. For a small number of Tweets, it will do this without manual assistance but X has limits including rate limiting. If you have many thousands of tweets it will delete as many as it can up to the search limit that X allows. It will then stop and ask you to download an archive file of the tweets from X and provide it with that to find the others and delete them. This is a limitation imposed by X, it may change with time, at the time of writing the limit was 3,200 before an archive is required manually.
If you have a small number of tweets, say <50, it should do that pretty quickly in just a second or two. As you have more it will take longer as X limits the rate of access. 

## Why would I need this?
Deleting tweets on X is a laborious one at a time process. This automates it.
You might wonder why you would want to do this. We are seeing increasing surveillance and restriction on free speech. Tweets are taken out of context; police action is taken based on claimed offence. Let’s face it someone, somewhere is always going to claim offence at other’s opinions. We also all grow and change and for personalities & politicians the media regularly rakes up old tweets in order to embarrass, mislead and even destroy careers. It is not uncommon to say something stupid and rash in an emotional state or from lack of maturity when young.

## How do you use it?

> [!NOTE]
>  You need an X developer account in order to obtain a client id as a credential for the app to operate:  https://docs.x.com/fundamentals/developer-portal

The X developer account is free. It may seem complex and confusing if you are not an IT Geek, in which case I suggest you ask your friendly AI to walk you through the process. What is not free, is using the X api. Unfortunately, X charge for all API / automated access. It is something like $0.01 per API call. If you've ever sat and deleted a couple of hundred tweets, paying ~$2 to delete them looks attractive! That's an estimate, suggest you try a limited delete to check price before committing to thousands but if you haven't bought sufficient credits it will stop when gone.

Armed with a client id, install TweetDelete for your system, it'll ask for the client id

During installation 2 things will be done; a tiny server component, which is more of a proxy for the comms to X, will be installed and a Javascript app that runs in the browser.

Launch the app from the menu system or terminal; “tweetdelete”. It will launch and run in your default browser.

## Operating System
Currently I have built TweetDelete for 2 platforms; Windows 64 bit and Debian derived Linux. The Windows version worked on my Intel  based Windows 11, it ought to run on other recent Windows options but has not been tested.  It uses Python and Javascript so there's a chance if the install. Similarly, the Debian version works on my Ubuntu 24.04 desktop and should run on most Debian based platforms that have Python >= v3.8  installed which is likely all of them.

## Credits
Thanks to Perplexity AI which did the coding. I don’t know which models it used; it was set to auto select. I was impressed, it was producing the executables in approx. 5 minutes per iteration. The longest time was spent on sorting out the packaging and installation process which took a day or two of iteration, mainly because the agent couldn't test Windows and relied on my slowness.

## Licencing
Copyright © 2026 Ian Packer.

Licensed under [PolyForm Non-commercial License 1.0.0]()

Commercial use requires a separate licence from the copyright holder.

This software is provided free of charge; that might change one day but I have no plans currently. It is free to use by individuals. It will not (probably can’t be) retrospectively charged if it was used in compliance with this licencing approach.

It is supplied without any warranty, guarantees or support. If you find a bug by all means email to the address below. Note: This is not my job I produced it for my own use and I am simply sharing, the risk is yours. It has not been professionally tested.

It is not licenced for use in commercial gain. If you wish to discuss such use, 
please email: think-1551@pm.me

## Installation
### Windows 11, 64 bit x86
It may work on other releases, maybe even ARM, I haven't tried. 
Download TweetDelete-Setup.exe and run it. It installs to; %LOCALAPPDATA%\Programs\TweetDelete of the user installing. You will get a warning from Windows because I haven't signed up for Microsoft's code-signing certificate subscription. 

You 'may' get anti-virus warnings, usually not.

It should be setup as any other Windows user app. There is a small server/proxy running and will show up on the system tray. This is needed due to X.com API and CORS protections. The app runs in the browser talking to the server/proxy.

DOWNLOAD for Windows

### Debian / Ubuntu
It has only been tested on Ubuntu 24.04 but should run on other Debian architecture Linux. On this version it is dependent on the presence of Python 3.8 or later. It should pull it in if not present.

Download the .deb package and run with:
`sudo apt install ./tweetdelete_1.0.0_all.deb`

It installs a small systemd server component, which should be started and will start whenever you login, it is called "tweetdelete.service". This is a proxy service for X.com to avoid CORS protections.

The app will install menu items and start in the default browser.

[DOWNLOAD for Debian / Ubuntu   ](https://github.com/user-attachments/files/31076055/tweetdelete_1.0.0_all.zip)




